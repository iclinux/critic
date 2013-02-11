# -*- mode: python; encoding: utf-8 -*-
#
# Copyright 2012 Jens Lindström, Opera Software ASA
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy of
# the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations under
# the License.

import re
import utf8utils
import htmlutils
import configuration
from cStringIO import StringIO
import traceback
from request import NoDefault, DisplayMessage, InvalidParameterValue, decodeURIComponent, Request
import urllib

from textutils import json_encode, json_decode

LINK_RELS = { "Home": "home",
              "Dashboard": "contents",
              "Branches": "index",
              "Tutorial": "help",
              "Back to Review": "up" }

class NotModified:
    pass

class MovedTemporarily(Exception):
    def __init__(self, location, no_cache=False):
        self.location = location
        self.no_cache = no_cache

class NeedLogin(MovedTemporarily):
    def __init__(self, source):
        if isinstance(source, Request):
            target = "/" + source.path
            if source.query:
                target += "?" + source.query
        else:
            target = str(source)
        super(NeedLogin, self).__init__("/login?target=%s" % urllib.quote(target), no_cache=True)

def YesOrNo(value):
    if value == "yes": return True
    elif value == "no": return False
    else: raise DisplayMessage, "invalid parameter value; expected 'yes' or 'no'"

def generateEmpty(target):
    pass

def generateHeader(target, db, user, generate_right=None, current_page=None, extra_links=[]):
    target.addExternalStylesheet("resource/jquery-ui.css")
    target.addExternalStylesheet("resource/jquery-tooltip.css")
    target.addExternalStylesheet("resource/basic.css")
    target.addInternalStylesheet(".defaultfont, body { %s }" % user.getPreference(db, "style.defaultFont"))
    target.addInternalStylesheet(".sourcefont { %s }" % user.getPreference(db, "style.sourceFont"))
    target.addExternalScript("resource/jquery.js")
    target.addExternalScript("resource/jquery-ui.js")
    target.addExternalScript("resource/jquery-tooltip.js")
    target.addExternalScript("resource/jquery-ui-autocomplete-html.js")
    target.addExternalScript("resource/basic.js")

    target.noscript().h1("noscript").blink().text("Please enable scripting support!")

    row = target.table("pageheader", width='100%').tr()
    left = row.td("left", valign='bottom', align='left')
    b = left.b()

    opera_class = "opera"

    if configuration.base.IS_DEVELOPMENT:
        opera_class += " development"

    b.b(opera_class, onclick="location.href='/';").text("Opera")
    b.b("critic", onclick="location.href='/';").text("Critic")

    links = []

    if not user.isAnonymous():
        links.append(["home", "Home", None, None])

    links.append(["dashboard", "Dashboard", None, None])
    links.append(["branches", "Branches", None, None])
    links.append(["search", "Search", None, None])

    if user.hasRole(db, "administrator"):
        links.append(["services", "Services", None, None])
    if user.hasRole(db, "repositories"):
        links.append(["repositories", "Repositories", None, None])

    if configuration.extensions.ENABLED:
        import extensions

        updated = extensions.Extension.getUpdatedExtensions(db, user)
        if updated:
            link_title = "\n".join([("%s by %s can be updated!" % (extension_name, author_fullname)) for author_fullname, extension_name in updated])
            links.append(["manageextensions", "Extensions (%d)" % len(updated), "color: red", link_title])
        else:
            links.append(["manageextensions", "Extensions", None, None])

    links.append(["config", "Config", None, None])
    links.append(["tutorial", "Tutorial", None, None])

    cursor = db.cursor()
    cursor.execute("""SELECT COUNT(*)
                        FROM newsitems
             LEFT OUTER JOIN newsread ON (item=id AND uid=%s)
                       WHERE uid IS NULL""",
                   (user.id,))
    count = cursor.fetchone()[0]

    if count:
        links.append(["news", "News (%d)" % count, "color: red", "There are %d unread news items!" % count])
    else:
        links.append(["news", "News", None, None])

    req = target.getRequest()

    if configuration.base.AUTHENTICATION_MODE == "critic" and configuration.base.SESSION_TYPE == "cookie":
        if user.isAnonymous():
            links.append(["login", "Sign in", None, None])
        elif not req or req.user == user.name:
            links.append(["javascript:signOut();", "Sign out", None, None])

    for url, label in extra_links:
        links.append([url, label, None, None])

    if req and configuration.extensions.ENABLED:
        injected = {}

        extensions.executeInject(db, getPath(req, db, user), req.query, user, target, links, injected)

        for url in injected.get("stylesheets", []):
            target.addExternalStylesheet(url, use_static=False, order=1)

        for url in injected.get("scripts", []):
            target.addExternalScript(url, use_static=False, order=1)
    else:
        injected = None

    ul = left.ul()

    for index, (url, label, style, title) in enumerate(links):
        ul.li().a(href=url, style=style, title=title).text(label)

        rel = LINK_RELS.get(label)
        if rel: target.setLink(rel, url)

    right = row.td("right", valign='bottom', align='right')
    if generate_right:
        generate_right(right)
    else:
        right.div("buttons").span("buttonscope buttonscope-global")

    return injected

def getPath(req, db=None, user=None):
    path = req.path

    if db and user and not path: return [user.getPreference(db, "defaultPage")]
    elif req.original_path != path: return [req.original_path, path]
    else: return [path]

def getParameter(req, name, default=NoDefault(), filter=lambda value: value):
    match = re.search("(?:^|&)" + name + "=([^&]*)", str(req.query))
    if match:
        try: return filter(decodeURIComponent(match.group(1)))
        except DisplayMessage: raise
        except: raise DisplayMessage, "Invalid parameter value: %s=%r" % (name, match.group(1))
    elif isinstance(default, NoDefault): raise DisplayMessage, "Required parameter missing: %s" % name
    else: return default

def renderShortcuts(target, page, **kwargs):
    shortcuts = target.div("shortcuts", style="margin-top: 10px; border-top: 3px solid black; text-align: right; padding-top: 10px; padding-right: 1em")
    shortcuts.text("Shortcuts: ")

    if page == "showcommit":
        what = "files"

        merge_parents = kwargs.get("merge_parents")
        if merge_parents > 1:
            for index in range(min(9, merge_parents)):
                shortcuts.b().text("(%d)" % (index + 1))
                shortcuts.text(" changes relative to %s parent, " % ("first", "second", "third", "fourth", "fifth", "seventh", "eight", "ninth")[index])
    elif page == "showcomments":
        what = "comments"

    def renderShortcut(keyCode, ch, text, is_last=False):
        a = shortcuts.a("shortcut", href="javascript:void(handleKeyboardShortcut(%d));" % keyCode)
        a.b().text("(%s)" % ch)
        a.text(" %s" % text)
        if not is_last:
            shortcuts.text(", ")

    if page == "showcommit" or page == "showcomments":
        renderShortcut(ord("e"), "e", "expand all %s" % what)
        renderShortcut(ord("c"), "c", "collapse all %s" % what)
        renderShortcut(ord("s"), "s", "show all %s" % what)
        renderShortcut(ord("h"), "h", "hide all %s" % what, page == "showcomments")

        if page == "showcommit":
            renderShortcut(ord("m"), "m", "detect moved code")

            if kwargs.get("squashed_diff"):
                renderShortcut(ord("b"), "b", "blame")

            renderShortcut(32, "SPACE", "scroll or show/expand next file", True)

    if page == "showcomment":
        renderShortcut(ord("m"), "m", "show more context")
        renderShortcut(ord("l"), "l", "show less context", True)

    if page == "filterchanges":
        renderShortcut(ord("g"), "g", "go / display diff", True)

def displayMessage(db, req, user, title, review=None, message=None, page_title=None, is_html=False):
    document = htmlutils.Document(req)

    if page_title:
        document.setTitle(page_title)

    document.addExternalStylesheet("resource/message.css")

    html = document.html()
    head = html.head()
    body = html.body()

    if review:
        import reviewing.utils as review_utils

        def generateRight(target):
            review_utils.renderDraftItems(db, user, review, target)

        back_to_review = ("r/%d" % review.id, "Back to Review")

        generateHeader(body, db, user, generate_right=generateRight, extra_links=[back_to_review])
    else:
        generateHeader(body, db, user)

    target = body.div("message")

    if message:
        target.h1("title").text(title)

        if callable(message): message(target)
        elif is_html: target.innerHTML(message)
        else: target.h3().text(message)
    else:
        target.h1("center").text(title)

    return document

class PaleYellowTable:
    def __init__(self, target, title, columns=[10, 60, 30]):
        self.table = target.div("main").table("paleyellow", align="center").tbody()
        self.columns = columns

        colgroup = self.table.colgroup()
        for column in columns: colgroup.col(width="%d%%" % column)

        h1 = self.table.tr().td("h1", colspan=len(columns)).h1()
        h1.text(title)
        self.titleRight = h1.span("right")

    def addSection(self, title, extra=None):
        h2 = self.table.tr().td("h2", colspan=len(self.columns)).h2()
        h2.text(title)
        if extra is not None:
            h2.span().text(extra)

    def addItem(self, heading, value, description, buttons=None):
        row = self.table.tr("item")
        row.td("name").innerHTML(htmlutils.htmlify(heading).replace(" ", "&nbsp;") + ":")
        cell = row.td("value", colspan=2).preformatted()
        if callable(value): value(cell)
        else: cell.text(str(value))
        if buttons:
            div = cell.div("buttons")
            for label, onclick in buttons:
                div.button(onclick=onclick).text(label)
        if description is not None:
            self.table.tr("help").td(colspan=len(self.columns)).text(description)

    def addCentered(self, content=None):
        row = self.table.tr("centered")
        cell = row.td(colspan=len(self.columns))
        if callable(content): content(cell)
        elif content: cell.text(str(content))
        return cell

    def addSeparator(self):
        self.table.tr("separator").td(colspan=len(self.columns)).div()

def generateRepositorySelect(db, user, target, selected=None, **attributes):
    select = target.select(**attributes)

    cursor = db.cursor()
    cursor.execute("""SELECT id, name, path
                        FROM repositories
                    ORDER BY name""")

    rows = cursor.fetchall()

    if not rows:
        select.option(value="-", disabled="disabled").text("No repositories")
        return

    if selected is None:
        if len(rows) == 1:
            selected = rows[0][0]
        else:
            selected = user.getPreference(db, "defaultRepository")
            if not selected:
                select.option(value="-", selected="selected", disabled="disabled").text("Select a repository")

    highlighted_ids = set()

    cursor.execute("""SELECT DISTINCT repository
                        FROM filters
                       WHERE uid=%s""",
                   (user.id,))
    highlighted_ids.update(repository_id for (repository_id,) in cursor)

    cursor.execute("""SELECT DISTINCT repository
                        FROM branches
                        JOIN reviews ON (reviews.branch=branches.id)
                        JOIN reviewusers ON (reviewusers.review=reviews.id)
                       WHERE reviewusers.uid=%s""",
                   (user.id,))
    highlighted_ids.update(repository_id for (repository_id,) in cursor)

    if not highlighted_ids or len(highlighted_ids) == len(rows):
        # Do not group options when there will be only one group.
        highlighted = select
        other = select
    else:
        highlighted = select.optgroup(label="Highlighted")
        other = select.optgroup(label="Other")

    name_width = max(len(name) for (repository_id, name, path) in rows)
    url_width = len(configuration.base.HOSTNAME) + 1 + max(len(path) for (repository_id, name, path) in rows)
    label_format = "%-{0}s %{1}s".format(name_width, url_width)

    for repository_id, name, path in rows:
        if repository_id in highlighted_ids:
            optgroup = highlighted
        else:
            optgroup = other

        url = "%s:%s" % (configuration.base.HOSTNAME, path)

        if repository_id == selected or name == selected:
            is_selected = "selected"
        else:
            is_selected = None

        optgroup.option(value=name, selected=is_selected).text(label_format % (name, url))
