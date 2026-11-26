# -*- coding: utf-8 -*-
from typing import Any, List, Optional, Dict
import json
import time
import datetime
import traceback

from org.telegram.tgnet import TLRPC
from org.telegram.messenger import MessagesController, UserConfig, AndroidUtilities, ChatObject

from base_plugin import BasePlugin, AppEvent, MenuItemData, MenuItemType, MethodHook
from client_utils import send_request, RequestCallback, run_on_queue, get_last_fragment, PLUGINS_QUEUE
from android_utils import run_on_ui_thread, OnClickListener, OnLongClickListener
from hook_utils import find_class, get_private_field
from ui.settings import Header, Input, Text, Switch, Divider
from ui.alert import AlertDialogBuilder
from ui.bulletin import BulletinHelper

__id__          = "temp_sub"
__name__        = "TempSub"
__description__ = "Временные подписки на каналы с автоматической отпиской по расписанию."
__author__      = "@itNotMax - @MaxExteraPlugins"
__version__ = "3.1.6"
__icon__        = "MaxExteraPluginsStickers/5"
__min_version__ = "12.10.6"
__app_version__ = ">=12.1.1"
__sdk_version__ = ">=1.4.3.3"

BUTTON_TAG = "tempsub_join_button"
SPLIT_APPLIED_TAG = "tempsub_split_applied"

_check_task_scheduled = False
_editing_channel_id = None
_button_refs = {}
_dex_bridge = None


class _DexBridge:
    def __init__(self):
        self._cls = None
        self._loaded = False

    def ensure_loaded(self):
        if self._loaded and self._cls is not None:
            return True
        try:
            import base64
            import zlib
            from java.nio import ByteBuffer
            from dalvik.system import InMemoryDexClassLoader
            from org.telegram.messenger import ApplicationLoader

            _dex_b64 = globals().get("_DEX_PAYLOAD")
            if _dex_b64:
                dex_bytes = zlib.decompress(base64.b64decode(_dex_b64))
            else:
                with open(__file__, "r", encoding="utf-8") as f:
                    src = f.read()

                begin_marker = "# __DEX_" + "BEGIN__"
                end_marker = "# __DEX_" + "END__"
                if begin_marker not in src or end_marker not in src:
                    return False

                block = src.split(begin_marker, 1)[1].split(end_marker, 1)[0]
                b64_lines = []
                for line in block.splitlines():
                    s = line.strip()
                    if s.startswith("#"):
                        s = s[1:].strip()
                    if s:
                        b64_lines.append(s)
                if not b64_lines:
                    return False

                dex_bytes = zlib.decompress(base64.b64decode("".join(b64_lines)))
            buf = ByteBuffer.wrap(dex_bytes)
            parent_cl = ApplicationLoader.getClass().getClassLoader()
            loader = InMemoryDexClassLoader(buf, parent_cl)
            self._cls = loader.loadClass("com.exteraplugins.temp_sub.Main")
            self._cls.getMethod("load").invoke(None)
            self._loaded = True
            return True
        except Exception:
            return False

    def call(self, method_name, *args):
        if not self.ensure_loaded():
            return None
        try:
            methods = self._cls.getMethods()
            for m in methods:
                if m.getName() == method_name and len(m.getParameterTypes()) == len(args):
                    return m.invoke(None, *args)
        except Exception:
            pass
        return None

    def get_field(self, field_name, default=None):
        if not self.ensure_loaded():
            return default
        try:
            f = self._cls.getField(field_name)
            return f.get(None)
        except Exception:
            return default


def _get_dex():
    global _dex_bridge
    if _dex_bridge is None:
        _dex_bridge = _DexBridge()
    return _dex_bridge


def _activity():
    f = get_last_fragment()
    if f:
        try:
            return f.getParentActivity()
        except:
            pass
    return None


def _log_err(plugin_ref, tag):
    try:
        plugin_ref.log(tag + ": " + traceback.format_exc())
    except Exception:
        pass



# ============================================================
# MaxExteraPlugins Shared Author & Channel Subscription Module
# ============================================================

# ============================================================
# MaxExteraPlugins Shared Author & Channel Subscription Module
# ============================================================

# ============================================================
# MaxExteraPlugins Shared Author & Channel Subscription Module
# ============================================================
class AuthorChannelHelper:
    CONFIG_URL = "https://raw.githubusercontent.com/MaxExteraPlugins/config/main/links.json"

    DEFAULT_LINKS = {
        "channel_username": "MaxExteraPlugins",
        "channel_url": "https://t.me/MaxExteraPlugins",
        "header": {"ru": "MaxExteraPlugins", "en": "MaxExteraPlugins"},
        "buttons": [
            {"title": {"ru": "Канал автора", "en": "Author's Channel"}, "icon": "msg_channel", "url": "https://t.me/MaxExteraPlugins"},
            {"title": {"ru": "Поддержать автора", "en": "Support author"}, "icon": "msg_share", "url": "https://t.me/itNotMax"},
            {"title": {"ru": "Сообщить о баге", "en": "Report a bug"}, "icon": "msg_help", "url": "https://t.me/itNotMax"}
        ]
    }

    _TEXTS = {
        "install_card_title": {"ru": "Подпишитесь на канал", "en": "Subscribe to channel"},
        "install_card_msg": {
            "ru": (
                "Мы делаем плагины бесплатными и доступными для всех: пожалуйста, "
                "поддержите нас подпиской на официальный канал @MaxExteraPlugins.\n\n"
                "Там публикуются обновления, важные новости и анонсы новых плагинов."
            ),
            "en": (
                "We keep our plugins free and accessible for everyone: please "
                "support us by subscribing to our official channel @MaxExteraPlugins.\n\n"
                "Updates, news, and new plugin announcements are published there."
            )
        },
        "open_channel_btn": {"ru": "Перейти на канал", "en": "Open channel"},
        "remind_later_btn": {"ru": "Напомнить позже", "en": "Remind later"},
        "never_show_btn": {"ru": "Больше не показывать", "en": "Don't show again"},
        "sub_thank_you": {"ru": "Спасибо за подписку на наш канал!", "en": "Thank you for subscribing to our channel!"},
        "settings_channel": {"ru": "Канал автора", "en": "Author's Channel"},
        "settings_donate": {"ru": "Поддержать автора", "en": "Support author"},
        "settings_bug_report": {"ru": "Сообщить о баге", "en": "Report a bug"}
    }

    @classmethod
    def is_ru(cls) -> bool:
        try:
            from org.telegram.messenger import LocaleController
            lang = str(LocaleController.getInstance().getCurrentLocale().getLanguage() or "").lower()
            return lang in ("ru", "be", "uk", "kk")
        except Exception:
            return True

    @classmethod
    def tr(cls, key: str) -> str:
        entry = cls._TEXTS.get(key, {})
        return entry.get("ru" if cls.is_ru() else "en", entry.get("en", key))

    def __init__(self, plugin, icon_sticker=None):
        self.plugin = plugin
        self.icon_sticker = icon_sticker or getattr(plugin, "__icon__", "MaxExteraPluginsStickers/1")
        self._stopped = False
        self._active_dialog = None

    def on_load(self):
        self._stopped = False
        self._active_dialog = None
        self._load_remote_links_async()
        self._check_initial_subscription_flow()

    def on_event(self, event_type):
        try:
            name = str(event_type).upper()
            if name.endswith("START") or name.endswith("RESUME"):
                if self.is_subscribed() and not self.plugin.get_setting("was_subscribed", False):
                    self.plugin.set_setting("was_subscribed", True)
                    self._dismiss_active_dialog()
                    self._notify_subscribed()
        except Exception:
            pass

    def on_unload(self):
        self._stopped = True
        self._dismiss_active_dialog()

    def is_allowed(self) -> bool:
        return True

    def _get_links(self) -> dict:
        raw = self.plugin.get_setting("remote_links_cache", None)
        if raw:
            try:
                import json
                data = json.loads(raw)
                if isinstance(data, dict): return data
            except Exception:
                pass
        return self.DEFAULT_LINKS

    def _load_remote_links_async(self):
        def worker():
            try:
                import urllib.request, json
                req = urllib.request.Request(self.CONFIG_URL, headers={"User-Agent": "MaxExteraPlugins"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, dict):
                            self.plugin.set_setting("remote_links_cache", json.dumps(data))
                            self._resolve_channel_in_telegram(data.get("channel_username", "MaxExteraPlugins"))
            except Exception:
                pass
        import threading
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _open_url(self, url: str, view=None):
        try:
            from org.telegram.messenger.browser import Browser
            from org.telegram.messenger import ApplicationLoader
            from client_utils import get_last_fragment
            ctx = None
            if view is not None:
                ctx = view.getContext()
            if ctx is None:
                frag = get_last_fragment()
                if frag is not None:
                    ctx = frag.getParentActivity()
            if ctx is None:
                ctx = ApplicationLoader.applicationContext
            Browser.openUrl(ctx, url)
        except Exception:
            pass

    def _resolve_channel_in_telegram(self, username: str):
        try:
            from org.telegram.messenger import UserConfig, MessagesController
            from org.telegram.tgnet import TLRPC, ConnectionsManager, RequestDelegate
            clean_user = username.lstrip("@")
            acc = int(UserConfig.selectedAccount)
            mc = MessagesController.getInstance(acc)
            if not mc: return
            req = TLRPC.TL_contacts_resolveUsername()
            req.username = clean_user
            helper_ref = self
            class Delegate(RequestDelegate):
                def run(self, response, error):
                    if error is None and response is not None:
                        chats = getattr(response, "chats", None)
                        if chats is not None and chats.size() > 0:
                            for a in range(UserConfig.MAX_ACCOUNT_COUNT):
                                try:
                                    if UserConfig.getInstance(a).isClientActivated():
                                        MessagesController.getInstance(a).putChats(chats, False)
                                except Exception:
                                    pass
                            c = chats.get(0)
                            cid = getattr(c, "id", 0)
                            if cid > 0:
                                helper_ref.plugin.set_setting("resolved_channel_id", int(cid))
            ConnectionsManager.getInstance(acc).sendRequest(req, Delegate())
        except Exception:
            pass

    def is_subscribed(self) -> bool:
        try:
            from org.telegram.messenger import UserConfig, MessagesController, MessagesStorage
            from java.lang import Long as JLong
            links = self._get_links()
            target = str(links.get("channel_username", "MaxExteraPlugins") or "MaxExteraPlugins").lower().lstrip("@")
            saved_cid = int(self.plugin.get_setting("resolved_channel_id", 0) or 0)

            for acc in range(UserConfig.MAX_ACCOUNT_COUNT):
                try:
                    uconf = UserConfig.getInstance(acc)
                    if not uconf.isClientActivated():
                        continue

                    mc = MessagesController.getInstance(acc)
                    storage = MessagesStorage.getInstance(acc)

                    # 1. In-memory check by resolved_channel_id
                    if saved_cid and mc is not None:
                        try:
                            c = mc.getChat(JLong(saved_cid))
                            if c is not None:
                                left = bool(getattr(c, "left", True))
                                kicked = bool(getattr(c, "kicked", False))
                                if not left and not kicked:
                                    return True
                        except Exception:
                            pass

                    # 2. In-memory check by username
                    if mc is not None:
                        try:
                            chat = mc.getUserOrChat(target)
                            if chat is not None:
                                cid = int(getattr(chat, "id", 0) or 0)
                                if cid and not saved_cid:
                                    saved_cid = cid
                                    self.plugin.set_setting("resolved_channel_id", cid)
                                left = bool(getattr(chat, "left", True))
                                kicked = bool(getattr(chat, "kicked", False))
                                if not left and not kicked:
                                    return True
                        except Exception:
                            pass

                    # 3. In-memory dialogs check
                    if mc is not None:
                        try:
                            dialog_lists = []
                            d_chan = getattr(mc, "dialogsChannelsOnly", None)
                            if d_chan is not None and d_chan.size() > 0:
                                dialog_lists.append(d_chan)
                            d_all = getattr(mc, "allDialogs", None)
                            if d_all is not None and d_all.size() > 0:
                                dialog_lists.append(d_all)

                            for d_list in dialog_lists:
                                limit = min(d_list.size(), 300)
                                for i in range(limit):
                                    d = d_list.get(i)
                                    did = getattr(d, "id", 0)
                                    if did < 0:
                                        cid = -did
                                        if saved_cid and cid == saved_cid:
                                            c = mc.getChat(JLong(cid))
                                            if c is not None:
                                                left = bool(getattr(c, "left", True))
                                                kicked = bool(getattr(c, "kicked", False))
                                                if not left and not kicked:
                                                    return True
                                        else:
                                            c = mc.getChat(JLong(cid))
                                            if c is not None:
                                                uname = str(getattr(c, "username", "") or "").lower()
                                                if uname == target:
                                                    if not saved_cid:
                                                        saved_cid = cid
                                                        self.plugin.set_setting("resolved_channel_id", cid)
                                                    left = bool(getattr(c, "left", True))
                                                    kicked = bool(getattr(c, "kicked", False))
                                                    if not left and not kicked:
                                                        return True
                        except Exception:
                            pass

                    # 4. Storage check (SQLite on disk)
                    if storage is not None:
                        if saved_cid:
                            try:
                                if storage.containsLocalDialog(JLong(-saved_cid)):
                                    c_sync = storage.getChatSync(JLong(saved_cid))
                                    if c_sync is not None:
                                        left = bool(getattr(c_sync, "left", True))
                                        kicked = bool(getattr(c_sync, "kicked", False))
                                        if not left and not kicked:
                                            return True
                                    else:
                                        return True
                            except Exception:
                                pass

                        try:
                            db = storage.getDatabase()
                            if db is not None:
                                if saved_cid:
                                    try:
                                        cur = db.queryFinalized("SELECT did FROM dialogs WHERE did = ?", [JLong(-saved_cid)])
                                        if cur is not None:
                                            has_row = bool(cur.next())
                                            cur.dispose()
                                            if has_row:
                                                return True
                                    except Exception:
                                        pass

                                try:
                                    cur = db.queryFinalized("SELECT uid, name FROM chats WHERE LOWER(name) LIKE ?", ["%" + target + "%"])
                                    if cur is not None:
                                        while cur.next():
                                            c_id = cur.longValue(0)
                                            if c_id > 0:
                                                if not saved_cid:
                                                    saved_cid = int(c_id)
                                                    self.plugin.set_setting("resolved_channel_id", saved_cid)
                                                cur_d = db.queryFinalized("SELECT did FROM dialogs WHERE did = ?", [JLong(-c_id)])
                                                if cur_d is not None:
                                                    in_dlg = bool(cur_d.next())
                                                    cur_d.dispose()
                                                    if in_dlg:
                                                        cur.dispose()
                                                        return True
                                        cur.dispose()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def prepend_settings(self, original_items=None) -> list:
        from ui.settings import Header, Text, Divider

        if original_items and any(getattr(x, "text", "") == "MaxExteraPlugins" for x in original_items if type(x).__name__ == "Header"):
            return original_items

        links = self._get_links()
        buttons_config = links.get("buttons")

        author_items = []
        if isinstance(buttons_config, list):
            for btn in buttons_config:
                if not isinstance(btn, dict): continue
                raw_url = btn.get("url")
                if isinstance(raw_url, dict):
                    keys = [
                        getattr(self.plugin, "id", None),
                        globals().get("__id__", None),
                        globals().get("__name__", None),
                        self.plugin.__class__.__name__
                    ]
                    keys += [k.lower() for k in keys if isinstance(k, str)]
                    resolved_url = ""
                    for k in keys:
                        if k and k in raw_url:
                            resolved_url = str(raw_url[k] or "").strip()
                            if resolved_url:
                                break
                    url = resolved_url
                elif isinstance(raw_url, str):
                    url = raw_url.strip()
                else:
                    url = ""

                if not url: continue

                title_data = btn.get("title")
                if isinstance(title_data, dict):
                    title = title_data.get("ru" if self.is_ru() else "en") or title_data.get("en") or title_data.get("ru") or ""
                elif isinstance(title_data, str) and title_data:
                    title = title_data
                else:
                    title = btn.get("title_ru" if self.is_ru() else "title_en") or btn.get("title_ru") or btn.get("title_en") or ""

                if not title: continue
                icon = str(btn.get("icon") or "msg_channel").strip()

                def make_click(u):
                    return lambda v=None: self._open_url(u, v)

                author_items.append(Text(text=title, icon=icon, on_click=make_click(url)))
        else:
            for k, text_key, icon_name in [
                ("channel_url", "settings_channel", "msg_channel"),
                ("donate_url", "settings_donate", "msg_share"),
                ("bug_report_url", "settings_bug_report", "msg_help")
            ]:
                u = str(links.get(k) or "").strip()
                if u:
                    author_items.append(Text(text=self.tr(text_key), icon=icon_name, on_click=lambda v=None, target_u=u: self._open_url(target_u, v)))

        res = []
        if author_items:
            hdr = links.get("header") or "MaxExteraPlugins"
            if isinstance(hdr, dict):
                hdr_text = hdr.get("ru" if self.is_ru() else "en") or hdr.get("ru") or hdr.get("en") or "MaxExteraPlugins"
            else:
                hdr_text = str(hdr)
            res.append(Header(text=hdr_text))
            res.extend(author_items)
            res.append(Divider())

        cleaned_items = []
        if original_items:
            skipping_info = False
            for item in original_items:
                item_cls = type(item).__name__
                if item_cls == "Header":
                    txt = str(getattr(item, "text", "") or getattr(item, "title", "") or "").lower()
                    if "информ" in txt or "inform" in txt:
                        skipping_info = True
                        continue
                    else:
                        skipping_info = False
                elif skipping_info and item_cls == "Divider":
                    skipping_info = False
                    continue

                if not skipping_info:
                    cleaned_items.append(item)

        res.extend(cleaned_items)
        return res

    def _dismiss_active_dialog(self):
        try:
            if self._active_dialog is not None:
                self._active_dialog.dismiss()
        except Exception:
            pass
        self._active_dialog = None

    def _is_dialog_active(self) -> bool:
        try:
            if self._active_dialog is not None:
                return bool(self._active_dialog.isShowing())
        except Exception:
            pass
        return False

    def _notify_subscribed(self):
        try:
            from ui.bulletin import BulletinHelper
            BulletinHelper.show_success(self.tr("sub_thank_you"))
        except Exception:
            pass

    def _check_initial_subscription_flow(self):
        def flow():
            try:
                if self.plugin.get_setting("never_show_sub_prompt", False):
                    return

                current_ver = str(getattr(self.plugin, "__version__", globals().get("__version__", "1.0.0")) or "1.0.0")
                last_prompted_ver = self.plugin.get_setting("last_prompted_version", None)

                # Only show on install (last_prompted_ver is None) or update (last_prompted_ver != current_ver)
                if last_prompted_ver is not None and str(last_prompted_ver) == current_ver:
                    return

                if self.is_subscribed():
                    self.plugin.set_setting("last_prompted_version", current_ver)
                    self.plugin.set_setting("was_subscribed", True)
                    return

                self._show_subscription_prompt(current_ver)
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(flow, 1500)

    def _create_card_content(self, act, title_text, message_text):
        try:
            from android.widget import LinearLayout, TextView, ImageView
            from android.view import Gravity
            from org.telegram.messenger import AndroidUtilities
            from org.telegram.ui.ActionBar import Theme
            from client_utils import create_sticker_drawable

            dp = AndroidUtilities.dp

            layout = LinearLayout(act)
            layout.setOrientation(LinearLayout.VERTICAL)
            layout.setGravity(Gravity.CENTER_HORIZONTAL)
            layout.setPadding(dp(24), dp(20), dp(24), dp(10))

            sticker_drawable = None
            if self.icon_sticker:
                try:
                    sticker_drawable = create_sticker_drawable(self.icon_sticker, dp(120), dp(120))
                except Exception:
                    pass

            if sticker_drawable is not None:
                iv = ImageView(act)
                iv.setImageDrawable(sticker_drawable)
                lp_iv = LinearLayout.LayoutParams(dp(120), dp(120))
                lp_iv.gravity = Gravity.CENTER_HORIZONTAL
                lp_iv.bottomMargin = dp(14)
                layout.addView(iv, lp_iv)

            if title_text:
                tt = TextView(act)
                tt.setText(title_text)
                tt.setTextSize(19.0)
                tt.setTypeface(AndroidUtilities.bold())
                tt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                tt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_t = LinearLayout.LayoutParams(-1, -2)
                lp_t.bottomMargin = dp(8)
                layout.addView(tt, lp_t)

            if message_text:
                mt = TextView(act)
                mt.setText(message_text)
                mt.setTextSize(15.0)
                mt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                mt.setLineSpacing(float(dp(3)), 1.0)
                mt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_m = LinearLayout.LayoutParams(-1, -2)
                mt.setLayoutParams(lp_m)
                layout.addView(mt)

            return layout
        except Exception:
            return None

    def _show_subscription_prompt(self, current_ver: str):
        def show():
            try:
                if self._is_dialog_active(): return
                from client_utils import get_last_fragment
                frag = get_last_fragment()
                if not frag: return
                act = frag.getParentActivity()
                if not act: return

                links = self._get_links()
                url = links.get("channel_url", "https://t.me/MaxExteraPlugins")

                from ui.alert import AlertDialogBuilder
                b = AlertDialogBuilder(act)
                content = self._create_card_content(act, self.tr("install_card_title"), self.tr("install_card_msg"))
                if content is not None:
                    b.set_view(content)
                else:
                    b.set_title(self.tr("install_card_title"))
                    b.set_message(self.tr("install_card_msg"))

                def on_open_channel(dialog, which):
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("last_prompted_version", current_ver)
                    self._open_url(url)

                def on_remind_later(dialog, which):
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("last_prompted_version", current_ver)

                def on_never_show(dialog, which):
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("never_show_sub_prompt", True)
                    self.plugin.set_setting("last_prompted_version", current_ver)

                b.set_positive_button(self.tr("open_channel_btn"), on_open_channel)
                b.set_neutral_button(self.tr("remind_later_btn"), on_remind_later)
                b.set_negative_button(self.tr("never_show_btn"), on_never_show)

                def on_dismiss(builder):
                    self.plugin.set_setting("last_prompted_version", current_ver)
                try:
                    b.set_on_dismiss_listener(on_dismiss)
                except Exception:
                    pass

                self._active_dialog = b.show()
                self.plugin.set_setting("last_prompted_version", current_ver)
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(show)

class TempSubPlugin(BasePlugin):

    def on_plugin_load(self):
        self._author_helper = AuthorChannelHelper(self, icon_sticker=globals().get("__icon__", "MaxExteraPluginsStickers/1"))
        self._author_helper.on_load()
        self._ensure_data()
        self._sync_dex_state()
        self._setup_join_button_hook()
        self._setup_drawer_menu()
        run_on_queue(self._check_and_leave_expired, PLUGINS_QUEUE, 3000)
        self._schedule_periodic_check()

    def on_plugin_unload(self):
        if hasattr(self, "_author_helper"):
            self._author_helper.on_unload()
        global _check_task_scheduled, _button_refs
        _check_task_scheduled = False
        _button_refs.clear()
        try:
            dex = _get_dex()
            dex.call("pySetBadgeEnabled", False)
            dex.call("pySyncSubs", "[]")
        except Exception:
            pass

    def _sync_dex_state(self):
        try:
            dex = _get_dex()
            if dex.ensure_loaded():
                dex.call("pySetBadgeEnabled", bool(self.get_setting("chat_list_badge", True)))
                dex.call("pySyncSubs", json.dumps(self._get_subs()))
        except Exception:
            pass

    def on_app_event(self, event: AppEvent):
        if event == AppEvent.START or event == AppEvent.RESUME:
            run_on_queue(self._check_and_leave_expired, PLUGINS_QUEUE, 2000)
            self._schedule_periodic_check()

    # ---------------------------------------------------------------
    # Button on the Join bar
    # ---------------------------------------------------------------

    def _setup_join_button_hook(self):
        try:
            ChatActivityClass = find_class("org.telegram.ui.ChatActivity")
            if not ChatActivityClass:
                return

            me = self

            class BottomOverlayHook(MethodHook):
                def after_hooked_method(self_hook, param):
                    try:
                        chat_activity = param.thisObject
                        if chat_activity is None:
                            return
                        run_on_ui_thread(lambda: me._try_attach_button(chat_activity), 0)
                        run_on_ui_thread(lambda: me._try_attach_button(chat_activity), 150)
                    except:
                        pass

            try:
                self.hook_all_methods(ChatActivityClass, "updateBottomOverlay", BottomOverlayHook())
            except:
                pass

            try:
                self.hook_all_methods(ChatActivityClass, "onResume", BottomOverlayHook())
            except:
                pass

        except Exception:
            _log_err(self, "temp_sub: hook setup error")

    def _get_join_container(self, chat_activity):
        try:
            bcbl = get_private_field(chat_activity, "bottomChannelButtonsLayout")
            if bcbl is not None:
                try:
                    container = bcbl.getContainer()
                    if container is not None:
                        return container
                except:
                    pass
        except:
            pass
        try:
            return get_private_field(chat_activity, "bottomOverlayChat")
        except:
            return None

    def _get_join_bar(self, chat_activity):
        try:
            return get_private_field(chat_activity, "bottomOverlayChatText")
        except:
            return None

    def _find_attached_button(self, container):
        try:
            count = container.getChildCount()
            for i in range(count):
                child = container.getChildAt(i)
                if child is not None:
                    try:
                        if BUTTON_TAG == child.getTag():
                            return child
                    except:
                        pass
        except:
            pass
        return None

    def _create_button(self, container):
        try:
            from android.widget import FrameLayout, ImageView
            from android.view import View
            from android.graphics import PorterDuff, PorterDuffColorFilter
            from org.telegram.ui.ActionBar import Theme
            from org.telegram.ui.Components import LayoutHelper, ScaleStateListAnimator
            from java import jclass

            try:
                activity = container.getContext()
            except:
                activity = _activity()
            if activity is None:
                return None

            accent = Theme.getColor(Theme.key_featuredStickers_addButton)
            text_color = Theme.getColor(Theme.key_featuredStickers_buttonText)
            ripple_color = (int(text_color) & 0x00FFFFFF) | 0x33000000

            btn = FrameLayout(activity)
            btn.setTag(BUTTON_TAG)
            btn.setContentDescription("Временная подписка")
            btn.setClickable(True)
            btn.setFocusable(True)

            dp = AndroidUtilities.dp
            bg_norm = Theme.createRoundRectDrawable(dp(4), dp(21), dp(21), dp(4), int(accent))
            bg_press = Theme.createRoundRectDrawable(dp(21), dp(21), dp(21), dp(21), int(accent))
            btn.setBackground(bg_norm)

            ripple_view = View(activity)
            try:
                rip_norm = Theme.createRadSelectorDrawable(int(ripple_color), 4, 21, 21, 4)
                rip_press = Theme.createRadSelectorDrawable(int(ripple_color), 21, 21, 21, 21)
                if rip_norm:
                    ripple_view.setBackground(rip_norm)
            except Exception:
                rip_norm = None
                rip_press = None
            btn.addView(ripple_view, LayoutHelper.createFrame(-1, -1))

            icon_view = ImageView(activity)
            icon_view.setScaleType(ImageView.ScaleType.CENTER)
            try:
                icon_cls = jclass("org.telegram.messenger.R$drawable")
                icon_id = getattr(icon_cls, "msg_calendar2", 0)
            except Exception:
                icon_id = 0
            if icon_id:
                try:
                    icon_view.setImageResource(icon_id)
                    icon_view.setColorFilter(PorterDuffColorFilter(int(text_color), PorterDuff.Mode.SRC_IN))
                except Exception:
                    pass
            btn.addView(icon_view, LayoutHelper.createFrame(-1, -1, 17))

            try:
                ScaleStateListAnimator.apply(btn, 0.05, 1.2)
            except Exception:
                pass

            try:
                from java import dynamic_proxy
                OnTouchInterface = find_class("android.view.View$OnTouchListener")
                if OnTouchInterface is not None:
                    pressed_state = [False]

                    class SplitTouchListener(dynamic_proxy(OnTouchInterface)):
                        def onTouch(self_t, v, event):
                            try:
                                action = event.getAction() & 0xFF
                                if action == 0 and not pressed_state[0]:
                                    pressed_state[0] = True
                                    btn.setBackground(bg_press)
                                    if rip_press:
                                        ripple_view.setBackground(rip_press)
                                elif action in (1, 3) and pressed_state[0]:
                                    pressed_state[0] = False
                                    btn.setBackground(bg_norm)
                                    if rip_norm:
                                        ripple_view.setBackground(rip_norm)
                            except Exception:
                                pass
                            return False

                    btn.setOnTouchListener(SplitTouchListener())
            except Exception:
                pass

            lp = LayoutHelper.createFrame(42, 42, 21, 0, 3, 3, 3)
            container.addView(btn, lp)
            return btn
        except Exception:
            _log_err(self, "temp_sub: create button error")
            return None

    def _style_left_join_bar(self, bar, split_active):
        if bar is None:
            return
        try:
            from org.telegram.ui.ActionBar import Theme
            from org.telegram.ui.Components import ScaleStateListAnimator
            dp = AndroidUtilities.dp
            lp = bar.getLayoutParams()
            if split_active:
                accent = Theme.getColor(Theme.key_featuredStickers_addButton)
                bar.setBackground(Theme.createRoundRectDrawable(dp(21), dp(4), dp(4), dp(21), int(accent)))
                try:
                    ScaleStateListAnimator.apply(bar, 0.02, 1.2)
                except Exception:
                    pass
                if lp is not None:
                    changed = (
                        lp.height != dp(42)
                        or getattr(lp, "rightMargin", 0) != dp(48)
                        or getattr(lp, "leftMargin", 0) != dp(3)
                    )
                    if changed:
                        lp.width = -1
                        lp.height = dp(42)
                        if hasattr(lp, "gravity"):
                            lp.gravity = 16
                        lp.leftMargin = dp(3)
                        lp.topMargin = dp(3)
                        lp.rightMargin = dp(48)
                        lp.bottomMargin = dp(3)
                        bar.setLayoutParams(lp)
                bar.setTag(0x7F0A0001, SPLIT_APPLIED_TAG)
            else:
                if bar.getTag(0x7F0A0001) == SPLIT_APPLIED_TAG:
                    bar.setBackground(None)
                    if lp is not None:
                        lp.width = -1
                        lp.height = -1
                        if hasattr(lp, "gravity"):
                            lp.gravity = 0
                        lp.leftMargin = 0
                        lp.topMargin = 0
                        lp.rightMargin = 0
                        lp.bottomMargin = 0
                        bar.setLayoutParams(lp)
                    bar.setTag(0x7F0A0001, None)
        except Exception:
            pass

    def _try_attach_button(self, chat_activity):
        try:
            current_chat = get_private_field(chat_activity, "currentChat")
            if current_chat is None:
                return

            try:
                not_in_chat = ChatObject.isNotInChat(current_chat)
            except:
                not_in_chat = False

            container = self._get_join_container(chat_activity)
            if container is None:
                return

            button = self._find_attached_button(container)
            if button is None:
                if not not_in_chat:
                    return
                button = self._create_button(container)
            if button is None:
                return

            bar = self._get_join_bar(chat_activity)
            visible = not_in_chat
            if visible and bar is not None:
                try:
                    if bar.getVisibility() != 0:
                        visible = False
                except:
                    pass

            if visible:
                try:
                    bcbl = get_private_field(chat_activity, "bottomChannelButtonsLayout")
                    if bcbl is not None:
                        bcbl.setCenterAccentBackground(False, False)
                except Exception:
                    pass
                self._style_left_join_bar(bar, True)
                try:
                    if container.getVisibility() == 0:
                        button.setVisibility(0)
                    else:
                        button.setVisibility(4)
                except:
                    button.setVisibility(0)
            else:
                self._style_left_join_bar(bar, False)
                try:
                    button.setVisibility(8)
                except:
                    pass

            if not self._button_has_click(button):
                self._attach_click(button, chat_activity)
        except Exception:
            _log_err(self, "temp_sub: attach button error")

    def _button_has_click(self, button):
        try:
            return button.getTag(2) == "tempsub_clk"
        except:
            return False

    def _attach_click(self, button, chat_activity):
        me = self
        chat_ref = chat_activity

        def on_click(view):
            try:
                chat = get_private_field(chat_ref, "currentChat")
                if chat is None:
                    return
                try:
                    if not ChatObject.isNotInChat(chat):
                        return
                except:
                    return
                channel_id = chat.id
                access_hash = chat.access_hash
                title = chat.title
                username = ""
                try:
                    if chat.username:
                        username = chat.username
                except:
                    pass
                run_on_ui_thread(lambda: me._show_quick_temp_sub_dialog(
                    channel_id, access_hash, title, username
                ))
            except:
                pass

        def on_long_click(view):
            try:
                on_click(view)
                return True
            except:
                return False

        try:
            button.setOnClickListener(OnClickListener(on_click))
            button.setOnLongClickListener(OnLongClickListener(on_long_click))
        except:
            pass

        try:
            button.setTag(2, "tempsub_clk")
        except:
            pass

    # ---------------------------------------------------------------
    # Quick dialog (from the button / long press)
    # ---------------------------------------------------------------

    PRESET_DAYS = [1, 2, 3, 7, 14, 30, 90]

    def _create_presets_row(self, activity, input_date):
        try:
            from android.widget import LinearLayout, TextView
            from android.graphics.drawable import GradientDrawable
            from org.telegram.ui.ActionBar import Theme

            accent = Theme.getColor(Theme.key_featuredStickers_addButton)
            text_normal = Theme.getColor(Theme.key_chat_messagePanelText)
            text_accent = Theme.getColor(Theme.key_featuredStickers_buttonText)
            bg_normal = Theme.getColor(Theme.key_chat_messagePanelBackground)

            row = LinearLayout(activity)
            row.setOrientation(LinearLayout.HORIZONTAL)
            row.setGravity(16)
            row.setPadding(0, AndroidUtilities.dp(4), 0, AndroidUtilities.dp(8))

            buttons = []

            def style_btn(btn, selected):
                bg = GradientDrawable()
                if selected:
                    bg.setColor(accent)
                    btn.setTextColor(text_accent)
                else:
                    bg.setColor(bg_normal)
                    btn.setTextColor(text_normal)
                bg.setCornerRadius(AndroidUtilities.dp(12))
                btn.setBackground(bg)

            for days in self.PRESET_DAYS:
                btn = TextView(activity)
                btn.setText(f"{days}d")
                btn.setTextSize(13)
                btn.setTypeface(AndroidUtilities.bold())
                btn.setGravity(17)
                btn.setPadding(
                    AndroidUtilities.dp(10), AndroidUtilities.dp(6),
                    AndroidUtilities.dp(10), AndroidUtilities.dp(6)
                )
                style_btn(btn, False)

                lp = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
                lp.rightMargin = AndroidUtilities.dp(6)
                row.addView(btn, lp)
                buttons.append(btn)

                def make_handler(d, b):
                    def handler(v):
                        dt = datetime.datetime.now() + datetime.timedelta(days=d)
                        input_date.setText(dt.strftime("%Y-%m-%d %H:%M"))
                        for other in buttons:
                            style_btn(other, False)
                        style_btn(b, True)
                    return handler

                btn.setOnClickListener(OnClickListener(make_handler(days, btn)))

            return row, buttons
        except Exception:
            _log_err(self, "temp_sub: presets row error")
            return None, []

    def _preselect_preset(self, buttons, days):
        try:
            for i, d in enumerate(self.PRESET_DAYS):
                if d == days:
                    buttons[i].performClick()
                    return
        except:
            pass

    def _show_quick_temp_sub_dialog(self, channel_id, access_hash, title, username):
        a = _activity()
        if not a:
            BulletinHelper.show_error("Не удалось получить активность.")
            return

        dex = _get_dex()
        if dex.ensure_loaded():
            try:
                from java import dynamic_proxy
                Runnable = find_class("java.lang.Runnable")
                me = self
                ch_id = channel_id
                acc_hash = access_hash
                ch_title = title
                ch_username = username or ""
                display_title = ch_title or (f"@{ch_username}" if ch_username else "Канал")

                class ConfirmRunnable(dynamic_proxy(Runnable)):
                    def run(self_r):
                        try:
                            if not bool(dex.get_field("lastResultConfirmed", False)):
                                return
                            leave_ts = int(dex.get_field("lastResultLeaveAt", 0))
                            do_archive = bool(dex.get_field("lastResultArchive", True))
                            if leave_ts > int(time.time()):
                                run_on_queue(
                                    lambda: me._join_channel(ch_id, acc_hash, ch_title, ch_username, leave_ts, do_archive),
                                    PLUGINS_QUEUE
                                )
                        except Exception:
                            _log_err(me, "temp_sub: schedule sheet confirm error")

                default_archive = bool(self.get_setting("auto_archive", True))
                dex.call("pyShowScheduleSheet", a, display_title, 0, default_archive, False, ConfirmRunnable())
                return
            except Exception:
                _log_err(self, "temp_sub: native schedule sheet error")

        try:
            from android.widget import EditText, LinearLayout, TextView, CheckBox
            from android.graphics.drawable import GradientDrawable
            from org.telegram.ui.ActionBar import Theme

            lay = LinearLayout(a)
            lay.setOrientation(LinearLayout.VERTICAL)
            pad = AndroidUtilities.dp(20)
            lay.setPadding(pad, pad, pad, pad)

            info_tv = TextView(a)
            info_tv.setText(f"📅 {title}")
            info_tv.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            info_tv.setTextSize(16)
            info_tv.setTypeface(AndroidUtilities.bold())
            info_tv.setPadding(0, 0, 0, AndroidUtilities.dp(16))
            lay.addView(info_tv)

            input_date = EditText(a)
            input_date.setHint("ГГГГ-ММ-ДД ЧЧ:ММ")
            input_date.setSingleLine(True)
            input_date.setTextSize(15)
            input_date.setTextColor(Theme.getColor(Theme.key_chat_messagePanelText))
            input_date.setHintTextColor(Theme.getColor(Theme.key_chat_messagePanelHint))

            dbg = GradientDrawable()
            dbg.setColor(Theme.getColor(Theme.key_chat_messagePanelBackground))
            dbg.setCornerRadius(AndroidUtilities.dp(14))
            dbg.setStroke(AndroidUtilities.dp(1), Theme.getColor(Theme.key_chat_messagePanelShadow))
            input_date.setBackground(dbg)
            input_date.setPadding(
                AndroidUtilities.dp(16), AndroidUtilities.dp(14),
                AndroidUtilities.dp(16), AndroidUtilities.dp(14)
            )

            default_dt = datetime.datetime.now() + datetime.timedelta(days=7)
            input_date.setText(default_dt.strftime("%Y-%m-%d %H:%M"))

            lbl = TextView(a)
            lbl.setText("Дата отписки")
            lbl.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            lbl.setTextSize(14)
            lbl.setTypeface(AndroidUtilities.bold())
            lbl.setPadding(0, 0, 0, AndroidUtilities.dp(4))
            lay.addView(lbl)

            presets_row, preset_buttons = self._create_presets_row(a, input_date)
            if presets_row is not None:
                lay.addView(presets_row)

            lay.addView(input_date)

            self._preselect_preset(preset_buttons, 7)

            hint_tv = TextView(a)
            hint_tv.setText("Пример: 2025-12-31 или 2025-12-31 18:00")
            hint_tv.setTextColor(Theme.getColor(Theme.key_dialogTextGray2))
            hint_tv.setTextSize(12)
            hint_tv.setPadding(AndroidUtilities.dp(4), AndroidUtilities.dp(6), 0, 0)
            lay.addView(hint_tv)

            archive_cb = CheckBox(a)
            archive_cb.setText("Отправить в архив")
            archive_cb.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            archive_cb.setTextSize(15)
            archive_cb.setChecked(True)
            archive_cb.setPadding(0, AndroidUtilities.dp(16), 0, 0)
            lay.addView(archive_cb)

            me = self
            ch_id = channel_id
            acc_hash = access_hash
            ch_title = title
            ch_username = username

            def on_save(bld, _):
                date_str = str(input_date.getText())
                do_archive = archive_cb.isChecked()
                bld.dismiss()
                me._process_quick_temp_sub(ch_id, acc_hash, ch_title, ch_username, date_str, do_archive)

            bld = AlertDialogBuilder(a)
            bld.set_title("Временная подписка")
            bld.set_view(lay)
            bld.set_positive_button("Подписаться", on_save)
            bld.set_negative_button("Отмена", lambda b, _: b.dismiss())
            bld.show()

        except Exception as e:
            BulletinHelper.show_error(f"Ошибка: {e}")

    def _process_quick_temp_sub(self, channel_id, access_hash, title, username, date_str, do_archive=False):
        if not date_str:
            BulletinHelper.show_error("Не указана дата отписки.")
            return

        leave_ts = self._parse_date(date_str)
        if leave_ts is None:
            BulletinHelper.show_error("Неверный формат даты. Используйте ГГГГ-ММ-ДД или ГГГГ-ММ-ДД ЧЧ:ММ")
            return

        if leave_ts <= int(time.time()):
            BulletinHelper.show_error("Дата отписки должна быть в будущем.")
            return

        run_on_queue(
            lambda: self._join_channel(channel_id, access_hash, title, username or "", leave_ts, do_archive),
            PLUGINS_QUEUE
        )

    # ---------------------------------------------------------------
    # Join / leave logic
    # ---------------------------------------------------------------

    def _join_channel(self, channel_id, access_hash, title, username, leave_timestamp, do_archive=False):
        req = TLRPC.TL_channels_joinChannel()
        input_channel = TLRPC.TL_inputChannel()
        input_channel.channel_id = channel_id
        input_channel.access_hash = access_hash
        req.channel = input_channel

        me = self
        ch_id = channel_id
        acc_hash = access_hash
        ch_title = title
        uname = username
        leave_ts = leave_timestamp
        archive = do_archive

        def on_join(response, error):
            if error:
                BulletinHelper.show_error(f"Не удалось подписаться: {error.text}")
                return

            dt = datetime.datetime.fromtimestamp(leave_ts)
            date_str = dt.strftime("%Y-%m-%d %H:%M")

            entry = {
                "username": uname,
                "channel_id": int(ch_id),
                "access_hash": int(acc_hash),
                "leave_at": leave_ts,
                "joined": True,
                "title": ch_title,
                "label": f"{ch_title} — отписка {date_str}"
            }

            subs = me._get_subs()
            subs = [s for s in subs if s.get("channel_id") != int(ch_id)]
            subs.append(entry)
            me._save_subs(subs)

            if archive:
                run_on_queue(lambda: me._archive_chat(ch_id), PLUGINS_QUEUE, 1000)
                BulletinHelper.show_success(f"Подписка на {ch_title} + архив. Отписка: {date_str}")
            else:
                BulletinHelper.show_success(f"Подписка на {ch_title}. Отписка: {date_str}")

        callback = RequestCallback(on_join)
        send_request(req, callback)

    def _check_and_leave_expired(self):
        subs = self._get_subs()
        now = int(time.time())
        remaining = []
        any_left = False

        for sub in subs:
            if sub.get("leave_at", 0) <= now and sub.get("joined", False):
                self._leave_channel(sub)
                any_left = True
            else:
                remaining.append(sub)

        if any_left:
            self._save_subs(remaining)

    def _leave_channel(self, sub, silent=False):
        req = TLRPC.TL_channels_leaveChannel()
        input_channel = TLRPC.TL_inputChannel()
        input_channel.channel_id = sub["channel_id"]
        input_channel.access_hash = sub["access_hash"]
        req.channel = input_channel

        title = sub.get("title", sub.get("username", "???"))
        is_silent = silent
        ch_title = title

        def on_leave(response, error):
            if error:
                return
            if not is_silent:
                BulletinHelper.show_info(f"Отписка от {ch_title}")

        callback = RequestCallback(on_leave)
        send_request(req, callback)

    def _remove_subscription(self, channel_id, also_leave=True):
        subs = self._get_subs()
        target = None
        remaining = []
        for s in subs:
            if s.get("channel_id") == channel_id:
                target = s
            else:
                remaining.append(s)
        self._save_subs(remaining)

        if target and also_leave:
            self._leave_channel(target)
            BulletinHelper.show_info(f"Отписаться от: {target.get('title', '?')}")
        elif target:
            BulletinHelper.show_info(f"Удалено: {target.get('title', '?')}")

    def _archive_chat(self, channel_id):
        try:
            mc = MessagesController.getInstance(UserConfig.selectedAccount)
            dialog_id = -channel_id

            req = TLRPC.TL_folders_editPeerFolders()
            input_peer = TLRPC.TL_inputFolderPeer()

            dialogs = mc.getAllDialogs()
            for i in range(dialogs.size()):
                try:
                    d = dialogs.get(i)
                    if d.id == dialog_id:
                        peer = mc.getInputPeer(dialog_id)
                        if peer is not None:
                            input_peer.peer = peer
                            input_peer.folder_id = 1
                            req.folder_peers.add(input_peer)

                            def on_archive(response, error):
                                pass

                            send_request(req, RequestCallback(on_archive))
                        break
                except:
                    continue
        except:
            pass

    # ---------------------------------------------------------------
    # Add by username (drawer / settings)
    # ---------------------------------------------------------------

    def _setup_drawer_menu(self):
        if self.get_setting("drawer_button", False):
            self.add_menu_item(MenuItemData(
                menu_type=MenuItemType.DRAWER_MENU,
                text="Временная подписка",
                icon="msg_calendar2",
                item_id="tempsub_drawer",
                on_click=self._on_drawer_click
            ))

    def _on_drawer_click(self, ctx):
        run_on_ui_thread(self._dlg_add_from_drawer)

    def _dlg_add_from_drawer(self):
        a = _activity()
        if not a:
            BulletinHelper.show_error("Не удалось получить активность.")
            return
        self._show_dialog_impl(a, None)

    def _show_add_dialog(self, editing_channel_id=None):
        a = _activity()
        if not a:
            BulletinHelper.show_error("Не удалось получить активность.")
            return
        self._show_dialog_impl(a, editing_channel_id)

    def _show_dialog_impl(self, activity, editing_channel_id):
        try:
            from android.widget import EditText, LinearLayout, TextView, ScrollView, CheckBox
            from android.graphics.drawable import GradientDrawable
            from org.telegram.ui.ActionBar import Theme

            scr = ScrollView(activity)
            lay = LinearLayout(activity)
            lay.setOrientation(LinearLayout.VERTICAL)
            pad = AndroidUtilities.dp(20)
            lay.setPadding(pad, pad, pad, pad)

            row1 = LinearLayout(activity)
            row1.setOrientation(LinearLayout.HORIZONTAL)
            row1.setGravity(16)
            row1.setPadding(0, 0, 0, AndroidUtilities.dp(8))

            ic1 = TextView(activity)
            ic1.setText("📢")
            ic1.setTextSize(16)
            icp1 = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            )
            icp1.rightMargin = AndroidUtilities.dp(8)
            row1.addView(ic1, icp1)

            lbl1 = TextView(activity)
            lbl1.setText("Канал")
            lbl1.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            lbl1.setTextSize(14)
            lbl1.setTypeface(AndroidUtilities.bold())
            row1.addView(lbl1)
            lay.addView(row1)

            input_username = EditText(activity)
            input_username.setHint("@username или t.me/link")
            input_username.setSingleLine(True)
            input_username.setTextSize(15)
            input_username.setTextColor(Theme.getColor(Theme.key_chat_messagePanelText))
            input_username.setHintTextColor(Theme.getColor(Theme.key_chat_messagePanelHint))

            ubg = GradientDrawable()
            ubg.setColor(Theme.getColor(Theme.key_chat_messagePanelBackground))
            ubg.setCornerRadius(AndroidUtilities.dp(14))
            ubg.setStroke(AndroidUtilities.dp(1), Theme.getColor(Theme.key_chat_messagePanelShadow))
            input_username.setBackground(ubg)
            input_username.setPadding(
                AndroidUtilities.dp(16), AndroidUtilities.dp(14),
                AndroidUtilities.dp(16), AndroidUtilities.dp(14)
            )
            lay.addView(input_username)

            row2 = LinearLayout(activity)
            row2.setOrientation(LinearLayout.HORIZONTAL)
            row2.setGravity(16)
            row2.setPadding(0, AndroidUtilities.dp(16), 0, AndroidUtilities.dp(4))

            ic2 = TextView(activity)
            ic2.setText("📅")
            ic2.setTextSize(16)
            icp2 = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            )
            icp2.rightMargin = AndroidUtilities.dp(8)
            row2.addView(ic2, icp2)

            lbl2 = TextView(activity)
            lbl2.setText("Дата отписки")
            lbl2.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            lbl2.setTextSize(14)
            lbl2.setTypeface(AndroidUtilities.bold())
            row2.addView(lbl2)
            lay.addView(row2)

            input_date = EditText(activity)
            input_date.setHint("ГГГГ-ММ-ДД ЧЧ:ММ")
            input_date.setSingleLine(True)
            input_date.setTextSize(15)
            input_date.setTextColor(Theme.getColor(Theme.key_chat_messagePanelText))
            input_date.setHintTextColor(Theme.getColor(Theme.key_chat_messagePanelHint))

            dbg = GradientDrawable()
            dbg.setColor(Theme.getColor(Theme.key_chat_messagePanelBackground))
            dbg.setCornerRadius(AndroidUtilities.dp(14))
            dbg.setStroke(AndroidUtilities.dp(1), Theme.getColor(Theme.key_chat_messagePanelShadow))
            input_date.setBackground(dbg)
            input_date.setPadding(
                AndroidUtilities.dp(16), AndroidUtilities.dp(14),
                AndroidUtilities.dp(16), AndroidUtilities.dp(14)
            )

            presets_row, preset_buttons = self._create_presets_row(activity, input_date)
            if presets_row is not None:
                lay.addView(presets_row)

            lay.addView(input_date)

            hint_tv = TextView(activity)
            hint_tv.setText("Пример: 2025-12-31 или 2025-12-31 18:00")
            hint_tv.setTextColor(Theme.getColor(Theme.key_dialogTextGray2))
            hint_tv.setTextSize(12)
            hint_tv.setPadding(AndroidUtilities.dp(4), AndroidUtilities.dp(6), 0, 0)
            lay.addView(hint_tv)

            archive_cb = CheckBox(activity)
            archive_cb.setText("Отправить в архив")
            archive_cb.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            archive_cb.setTextSize(15)
            archive_cb.setChecked(True)
            archive_cb.setPadding(0, AndroidUtilities.dp(16), 0, 0)
            lay.addView(archive_cb)

            scr.addView(lay)

            if editing_channel_id:
                subs = self._get_subs()
                for s in subs:
                    if s.get("channel_id") == editing_channel_id:
                        input_username.setText(s.get("username", ""))
                        dt = datetime.datetime.fromtimestamp(s.get("leave_at", 0))
                        input_date.setText(dt.strftime("%Y-%m-%d %H:%M"))
                        break
            else:
                default_dt = datetime.datetime.now() + datetime.timedelta(days=7)
                input_date.setText(default_dt.strftime("%Y-%m-%d %H:%M"))
                self._preselect_preset(preset_buttons, 7)

            is_edit = editing_channel_id is not None
            title = "Редактировать подписку" if is_edit else "Новая временная подписка"
            btn_text = "Сохранить" if is_edit else "Подписаться"

            me = self
            edit_id = editing_channel_id

            def save(bld, _):
                username = str(input_username.getText())
                date_str = str(input_date.getText())
                do_archive = archive_cb.isChecked()
                bld.dismiss()
                me._process_add_or_edit(username, date_str, edit_id, do_archive)

            bld = AlertDialogBuilder(activity)
            bld.set_title(title)
            bld.set_view(scr)
            bld.set_positive_button(btn_text, save)
            bld.set_negative_button("Отмена", lambda b, _: b.dismiss())
            bld.show()

        except Exception as e:
            BulletinHelper.show_error(f"Ошибка: {e}")

    def _show_edit_time_sheet(self, channel_id):
        a = _activity()
        if not a:
            return
        subs = self._get_subs()
        target = None
        for s in subs:
            if s.get("channel_id") == channel_id:
                target = s
                break
        if not target:
            return

        dex = _get_dex()
        if dex.ensure_loaded():
            try:
                from java import dynamic_proxy
                Runnable = find_class("java.lang.Runnable")
                me = self
                cid = channel_id
                title = target.get("title", target.get("username", "Канал"))
                initial_leave = int(target.get("leave_at", 0) or 0)

                class EditConfirmRunnable(dynamic_proxy(Runnable)):
                    def run(self_r):
                        try:
                            if not bool(dex.get_field("lastResultConfirmed", False)):
                                return
                            new_ts = int(dex.get_field("lastResultLeaveAt", 0))
                            if new_ts <= int(time.time()):
                                return
                            cur_subs = me._get_subs()
                            dt = datetime.datetime.fromtimestamp(new_ts)
                            for item in cur_subs:
                                if item.get("channel_id") == cid:
                                    item["leave_at"] = new_ts
                                    item["label"] = f"{item.get('title', '?')} — отписка {dt.strftime('%Y-%m-%d %H:%M')}"
                                    break
                            me._save_subs(cur_subs)
                            BulletinHelper.show_success(f"Время обновлено: {dt.strftime('%d.%m.%Y %H:%M')}")
                        except Exception:
                            _log_err(me, "temp_sub: edit sheet confirm error")

                dex.call("pyShowScheduleSheet", a, title, initial_leave, False, True, EditConfirmRunnable())
                return
            except Exception:
                _log_err(self, "temp_sub: native edit sheet error")

        self._show_add_dialog(channel_id)

    def _open_channel_chat(self, channel_id):
        try:
            frag = get_last_fragment()
            if frag is None:
                return
            from android.os import Bundle
            ChatActivityCls = find_class("org.telegram.ui.ChatActivity")
            if ChatActivityCls is None:
                return
            bundle = Bundle()
            bundle.putLong("chat_id", int(channel_id))
            frag.presentFragment(ChatActivityCls(bundle))
        except Exception:
            _log_err(self, "temp_sub: open chat error")

    def _show_item_actions(self, channel_id):
        a = _activity()
        if not a:
            return

        subs = self._get_subs()
        target = None
        for s in subs:
            if s.get("channel_id") == channel_id:
                target = s
                break

        if not target:
            return

        title = target.get("title", target.get("username", "???"))
        me = self
        cid = channel_id

        def on_item_click(bld, which):
            bld.dismiss()
            if which == 0:
                run_on_ui_thread(lambda: me._open_channel_chat(cid))
            elif which == 1:
                run_on_ui_thread(lambda: me._show_edit_time_sheet(cid))
            elif which == 2:
                me._remove_subscription(cid, also_leave=True)
            elif which == 3:
                me._remove_subscription(cid, also_leave=False)

        builder = AlertDialogBuilder(a)
        builder.set_title(title)
        builder.set_items(
            ["Открыть канал", "Изменить время отписки", "Отписаться и удалить", "Удалить из списка (остаться)"],
            on_item_click
        )
        builder.set_negative_button("Отмена", lambda bld, w: bld.dismiss())
        builder.show()

    def _process_add_or_edit(self, username, date_str, editing_id, do_archive=False):
        if not username or not date_str:
            BulletinHelper.show_error("Заполните оба поля.")
            return

        leave_ts = self._parse_date(date_str)
        if leave_ts is None:
            BulletinHelper.show_error("Неверный формат даты. Используйте ГГГГ-ММ-ДД или ГГГГ-ММ-ДД ЧЧ:ММ")
            return

        if leave_ts <= int(time.time()):
            BulletinHelper.show_error("Дата отписки должна быть в будущем.")
            return

        if editing_id:
            subs = self._get_subs()
            old_entry = None
            remaining = []
            for s in subs:
                if s.get("channel_id") == editing_id:
                    old_entry = s
                else:
                    remaining.append(s)
            self._save_subs(remaining)

            old_username = old_entry.get("username", "") if old_entry else ""
            new_username = username.strip().lstrip("@").replace("https://t.me/", "").replace("t.me/", "")

            if old_username != new_username:
                if old_entry:
                    self._leave_channel(old_entry, silent=True)
                run_on_queue(lambda: self._add_subscription(username, leave_ts, do_archive), PLUGINS_QUEUE)
            else:
                if old_entry:
                    old_entry["leave_at"] = leave_ts
                    dt = datetime.datetime.fromtimestamp(leave_ts)
                    old_entry["label"] = f"{old_entry.get('title', '?')} — отписка {dt.strftime('%Y-%m-%d %H:%M')}"
                    remaining.append(old_entry)
                    self._save_subs(remaining)
                    BulletinHelper.show_success(f"Дата обновлена: {dt.strftime('%Y-%m-%d %H:%M')}")
        else:
            run_on_queue(lambda: self._add_subscription(username, leave_ts, do_archive), PLUGINS_QUEUE)

    def _add_subscription(self, username, leave_timestamp, do_archive=False):
        username = username.strip().lstrip("@").replace("https://t.me/", "").replace("t.me/", "")
        if not username:
            BulletinHelper.show_error("Username не может быть пустым.")
            return

        req = TLRPC.TL_contacts_resolveUsername()
        req.username = username

        me = self
        leave_ts = leave_timestamp
        uname = username
        archive = do_archive

        def on_resolve(response, error):
            if error:
                BulletinHelper.show_error(f"Ошибка: {error.text}")
                return

            resolved = response
            if not resolved or not resolved.chats or resolved.chats.isEmpty():
                BulletinHelper.show_error("Канал не найден.")
                return

            chat = resolved.chats.get(0)
            channel_id = chat.id
            access_hash = chat.access_hash
            title = chat.title

            mc = MessagesController.getInstance(UserConfig.selectedAccount)
            mc.putChats(resolved.chats, False)
            mc.putUsers(resolved.users, False)

            me._join_channel(channel_id, access_hash, title, uname, leave_ts, archive)

        callback = RequestCallback(on_resolve)
        send_request(req, callback)

    # ---------------------------------------------------------------
    # Periodic checks
    # ---------------------------------------------------------------

    def _schedule_periodic_check(self):
        global _check_task_scheduled
        if not self.get_setting("frequent_check", False):
            _check_task_scheduled = False
            return
        if _check_task_scheduled:
            return

        interval_minutes = self.get_setting("check_interval", 30)
        try:
            interval_ms = int(interval_minutes) * 60 * 1000
        except (ValueError, TypeError):
            interval_ms = 30 * 60 * 1000

        _check_task_scheduled = True
        self._run_periodic_check(interval_ms)

    def _run_periodic_check(self, interval_ms):
        global _check_task_scheduled
        if not _check_task_scheduled:
            return
        if not self.get_setting("frequent_check", False):
            _check_task_scheduled = False
            return

        self._check_and_leave_expired()

        run_on_queue(
            lambda: self._run_periodic_check(interval_ms),
            PLUGINS_QUEUE,
            interval_ms
        )

    # ---------------------------------------------------------------
    # Settings
    # ---------------------------------------------------------------

    def _ensure_data(self):
        subs = self.get_setting("subscriptions", None)
        if subs is None:
            self.set_setting("subscriptions", "[]")

    def _get_subs(self):
        raw = self.get_setting("subscriptions", "[]")
        try:
            return json.loads(raw)
        except:
            return []

    def _save_subs(self, subs):
        self.set_setting("subscriptions", json.dumps(subs), reload_settings=True)
        self._sync_dex_state()

    def _format_remaining(self, leave_ts):
        rem = int(leave_ts) - int(time.time())
        if rem <= 0:
            return "истекло"
        mins = rem // 60
        if mins >= 1440:
            d = mins // 1440
            h = (mins % 1440) // 60
            return f"{d}д {h}ч" if h > 0 else f"{d}д"
        if mins >= 60:
            h = mins // 60
            m = mins % 60
            return f"{h}ч {m}м" if m > 0 else f"{h}ч"
        return f"{max(1, mins)}м"

    def _on_badge_toggle(self, enabled):
        try:
            _get_dex().call("pySetBadgeEnabled", bool(enabled))
        except Exception:
            pass

    def _parse_date(self, date_str):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(date_str.strip(), fmt)
                return int(dt.timestamp())
            except ValueError:
                continue
        return None

    def _on_add_click(self, view):
        username = self.get_setting("input_username", "")
        date_str = self.get_setting("input_date", "")
        do_archive = self.get_setting("auto_archive", False)
        self._process_add_or_edit(username, date_str, None, do_archive)

    def _on_check_now_click(self, view):
        run_on_queue(self._check_and_leave_expired, PLUGINS_QUEUE)
        BulletinHelper.show_info("Проверка подписок запущена...")

    def _on_drawer_toggle(self, enabled):
        if enabled:
            self._setup_drawer_menu()
        else:
            self.remove_menu_item("tempsub_drawer")

    def _on_frequent_check_toggle(self, enabled):
        global _check_task_scheduled
        if enabled:
            self._schedule_periodic_check()
        else:
            _check_task_scheduled = False

    def _open_url(self, url):
        def handler(view):
            try:
                from org.telegram.messenger.browser import Browser
                a = _activity()
                if a:
                    Browser.openUrl(a, url)
                else:
                    BulletinHelper.show_error("Не удалось получить активность для открытия ссылки")
            except:
                BulletinHelper.show_error("Не удалось получить активность для открытия ссылки")
        return handler

    def create_settings(self):
        try:
            items = self._inner_create_settings()
        except Exception:
            items = []
        if hasattr(self, '_author_helper'):
            return self._author_helper.prepend_settings(items)
        return items

    def _inner_create_settings(self):
        me = self
        default_date = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
        items = [
            Header(text="Информация"),
            Text(
                text="Документация",
                icon="msg_help",
                on_click=me._open_url("https://telegra.ph/Dokumentaciya-k-plaginu-TempSub-06-25"),
            ),
            Text(
                text="Наш канал",
                icon="msg_channel",
                on_click=me._open_url("https://t.me/MaxExteraPlugins"),
            ),
            Divider(),
            Header(text="Быстрая настройка подписки"),
            Input(
                key="input_username",
                text="Username канала",
                default="",
                subtext="Пример: @durov или t.me/durov",
                icon="msg_channel",
            ),
            Input(
                key="input_date",
                text="Дата отписки",
                default=default_date,
                subtext="Формат: ГГГГ-ММ-ДД или ГГГГ-ММ-ДД ЧЧ:ММ",
                icon="msg_calendar2",
            ),
            Switch(
                key="auto_archive",
                text="Автоархивация",
                default=True,
                icon="msg_archive",
            ),
            Text(
                text="Подписаться",
                icon="msg_arrow_forward",
                on_click=me._on_add_click,
            ),
            Divider(),
            Header(text="Мониторинг"),
            Text(
                text="Проверить подписки",
                icon="msg_retry",
                on_click=me._on_check_now_click,
            ),
            Switch(
                key="drawer_button",
                text="Кнопка в меню",
                default=False,
                subtext="Отображать пункт «Временная подписка» в главном меню",
                icon="msg_menu",
                on_change=me._on_drawer_toggle,
            ),
            Switch(
                key="chat_list_badge",
                text="Метка в списке чатов",
                default=True,
                subtext="Показывать оставшееся время до отписки на аватарке",
                icon="msg_timer",
                on_change=me._on_badge_toggle,
            ),
            Divider(),
            Header(text="Отписка"),
            Switch(
                key="frequent_check",
                text="Частая проверка",
                default=False,
                subtext="Проверять подписки периодически, а не только при запуске приложения",
                icon="msg_clock",
                on_change=me._on_frequent_check_toggle,
            ),
        ]

        if me.get_setting("frequent_check", False):
            items.append(
                Input(
                    key="check_interval",
                    text="Интервал (минуты)",
                    default="30",
                    subtext="Как часто проверять (по умолчанию 30 минут)",
                    icon="msg_timer",
                )
            )

        subs = sorted(me._get_subs(), key=lambda s: s.get("leave_at", 0))
        if subs:
            items.append(Divider())
            items.append(Header(text=f"Активные подписки ({len(subs)})"))
            for sub in subs:
                leave_at = sub.get("leave_at", 0)
                dt = datetime.datetime.fromtimestamp(leave_at)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
                rem_str = me._format_remaining(leave_at)
                title = sub.get("title", sub.get("username", "???"))
                uname = sub.get("username", "")
                ch_id = sub.get("channel_id", 0)
                sub_line = f"Через {rem_str} · {date_str}"
                if uname:
                    sub_line = f"@{uname.lstrip('@')} · {sub_line}"

                def make_click_handler(cid):
                    return lambda v: run_on_ui_thread(lambda: me._show_item_actions(cid))

                items.append(
                    Text(
                        text=title,
                        subtext=sub_line,
                        icon="msg_channel",
                        on_click=make_click_handler(ch_id),
                    )
                )
            items.append(
                Divider(text="Нажмите на канал, чтобы открыть, изменить время или отписаться")
            )
        else:
            items.append(Divider())
            items.append(
                Text(
                    text="Нет активных подписок",
                    subtext="Откройте любой канал и нажмите кнопку календаря рядом с «Подписаться»",
                    icon="msg_info",
                )
            )

        return items

# __DEX_BEGIN__
# __DEX_END__
