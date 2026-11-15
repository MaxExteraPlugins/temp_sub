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
__version__ = "3.1.0"
__icon__        = "MaxExteraPluginsStickers/5"
__min_version__ = "12.10.6"
__app_version__ = ">=12.1.1"
__sdk_version__ = ">=1.4.3.3"

BUTTON_TAG = "tempsub_join_button"

_check_task_scheduled = False
_editing_channel_id = None
_button_refs = {}


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
        "trial_days": 7,
        "header": {"ru": "MaxExteraPlugins", "en": "MaxExteraPlugins"},
        "buttons": [
            {"title": {"ru": "Канал автора", "en": "Author's Channel"}, "icon": "msg_channel", "url": "https://t.me/MaxExteraPlugins"},
            {"title": {"ru": "Поддержать автора", "en": "Support author"}, "icon": "msg_share", "url": "https://t.me/itNotMax"},
            {"title": {"ru": "Сообщить о баге", "en": "Report a bug"}, "icon": "msg_help", "url": "https://t.me/itNotMax"}
        ]
    }

    _TEXTS = {
        "install_card_title": {"ru": "Спасибо за установку!", "en": "Thank you for installing!"},
        "install_card_msg": {
            "ru": "Надеемся, что плагин будет вам полезен!\n\nМы делаем плагины бесплатными и доступными для всех: пожалуйста, поддержите нас подпиской. Там будут только важные новости, обновления и розыгрыши.",
            "en": "We hope this plugin will be useful to you!\n\nWe keep our plugins free and open for everyone: please support us with a subscription. We only post key news, updates, and giveaways."
        },
        "subscribe_btn": {"ru": "Подписаться", "en": "Subscribe"},
        "remind_later_btn": {"ru": "Напомнить позже", "en": "Remind later"},
        "check_sub_btn": {"ru": "Проверить подписку", "en": "Check subscription"},
        "trial_expired_title": {"ru": "Пожалуйста, подпишитесь на канал", "en": "Please subscribe to the channel"},
        "trial_expired_msg": {
            "ru": "Пожалуйста, подпишитесь на наш официальный канал.\n\nМы делаем плагины бесплатно, ничего не требуя взамен. До подписки функции плагина временно приостановлены.",
            "en": "Please subscribe to our official channel.\n\nWe develop plugins for free and ask nothing in return. Plugin features are paused until you subscribe."
        },
        "sub_thank_you": {"ru": "Спасибо за подписку на наш канал!", "en": "Thank you for subscribing to our channel!"},
        "sub_thank_you_after_expired": {"ru": "Спасибо за подписку на наш канал!\nПлагин продолжит работу!", "en": "Thank you for subscribing to our channel!\nThe plugin will continue working!"},
        "sub_not_found": {"ru": "Подписка не обнаружена. Пожалуйста, подпишитесь на канал.", "en": "Subscription not found. Please subscribe to the channel."},
        "settings_channel": {"ru": "Канал автора", "en": "Author's Channel"},
        "settings_donate": {"ru": "Поддержать автора", "en": "Support author"},
        "settings_bug_report": {"ru": "Сообщить о баге", "en": "Report a bug"}
    }

    @classmethod
    def is_ru(cls) -> bool:
        try:
            from org.telegram.messenger import LocaleController
            cur = str(LocaleController.getInstance().getCurrentLocale().getLanguage() or "").lower()
            if cur.startswith(("ru", "uk", "be")): return True
            if cur: return False
        except Exception:
            pass
        try:
            from java.util import Locale
            code = str(Locale.getDefault().getLanguage() or "").lower()
            if code.startswith(("ru", "uk", "be")): return True
        except Exception:
            pass
        return False

    @classmethod
    def tr(cls, key: str) -> str:
        entry = cls._TEXTS.get(key, {})
        return entry.get("ru" if cls.is_ru() else "en", entry.get("en", key))

    def __init__(self, plugin, icon_sticker=None):
        self.plugin = plugin
        self.icon_sticker = icon_sticker or getattr(plugin, "__icon__", "MaxExteraPluginsStickers/1")
        self._stopped = False
        self._active_dialog = None
        self._last_known_sub = None

    def on_load(self):
        self._stopped = False
        self._active_dialog = None
        self._last_known_sub = None
        self._load_remote_links_async()
        self._check_initial_subscription_flow()
        self._start_subscription_watcher()

    def on_event(self, event_type):
        try:
            name = str(event_type).upper()
            if name.endswith("START") or name.endswith("RESUME"):
                self._check_subscription_change_instant()
        except Exception:
            pass

    def on_unload(self):
        self._stopped = True
        self._dismiss_active_dialog()

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
                            mc.putChats(chats, False)
                            c = chats.get(0)
                            cid = getattr(c, "id", 0)
                            if cid > 0:
                                helper_ref.plugin.set_setting("resolved_channel_id", int(cid))
            ConnectionsManager.getInstance(acc).sendRequest(req, Delegate())
        except Exception:
            pass

    def is_subscribed(self) -> bool:
        try:
            from org.telegram.messenger import UserConfig, MessagesController
            links = self._get_links()
            target = str(links.get("channel_username", "MaxExteraPlugins") or "MaxExteraPlugins").lower().lstrip("@")
            saved_cid = self.plugin.get_setting("resolved_channel_id", 0)

            for acc in range(UserConfig.MAX_ACCOUNT_COUNT):
                uconf = UserConfig.getInstance(acc)
                if not uconf.isClientActivated(): continue
                mc = MessagesController.getInstance(acc)
                if not mc: continue

                if saved_cid:
                    try:
                        from java.lang import Long as JLong
                        c = mc.getChat(JLong(int(saved_cid)))
                        if c is not None:
                            left = bool(getattr(c, "left", True))
                            kicked = bool(getattr(c, "kicked", False))
                            if not left and not kicked: return True
                    except Exception:
                        pass

                try:
                    chat = mc.getUserOrChat(target)
                    if chat is not None:
                        cid = getattr(chat, "id", 0)
                        if cid and not saved_cid:
                            self.plugin.set_setting("resolved_channel_id", int(cid))
                        left = bool(getattr(chat, "left", True))
                        kicked = bool(getattr(chat, "kicked", False))
                        if not left and not kicked: return True
                except Exception:
                    pass

                try:
                    dialogs = mc.allDialogs
                    if dialogs is not None:
                        limit = min(dialogs.size(), 300)
                        for i in range(limit):
                            d = dialogs.get(i)
                            did = getattr(d, "id", 0)
                            if did < 0:
                                from java.lang import Long as JLong
                                c = mc.getChat(JLong(-did))
                                if c is not None:
                                    uname = str(getattr(c, "username", "") or "").lower()
                                    if uname == target:
                                        cid = getattr(c, "id", 0)
                                        if cid and not saved_cid:
                                            self.plugin.set_setting("resolved_channel_id", int(cid))
                                        left = bool(getattr(c, "left", True))
                                        kicked = bool(getattr(c, "kicked", False))
                                        if not left and not kicked: return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def is_allowed(self) -> bool:
        import time
        if self.is_subscribed():
            if not self.plugin.get_setting("was_subscribed", False):
                was_exp = bool(self.plugin.get_setting("was_expired_state", False))
                self.plugin.set_setting("was_subscribed", True)
                self.plugin.set_setting("was_expired_state", False)
                self._notify_subscribed(was_expired=was_exp)
            return True

        if self.plugin.get_setting("was_expired_state", False) or self.plugin.get_setting("was_subscribed", False):
            self.plugin.set_setting("was_subscribed", False)
            self.plugin.set_setting("was_expired_state", True)
            self._show_expired_card()
            return False

        now = time.time()
        install_time = float(self.plugin.get_setting("install_time", 0.0) or 0.0)
        if install_time <= 0:
            install_time = now
            self.plugin.set_setting("install_time", install_time)

        trial_days = float(self._get_links().get("trial_days", 7))
        trial_sec = trial_days * 86400.0

        if (now - install_time) < trial_sec:
            return True

        self.plugin.set_setting("was_expired_state", True)
        self._show_expired_card()
        return False

    def prepend_settings(self, original_items=None) -> list:
        from ui.settings import Header, Text, Divider

        # Prevent double-prepending
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

        # Clean original_items by removing any old "Информация" / "Information" category
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

    def _notify_subscribed(self, was_expired=False):
        self.plugin.set_setting("was_subscribed", True)
        from ui.bulletin import BulletinHelper
        msg_key = "sub_thank_you_after_expired" if was_expired else "sub_thank_you"
        BulletinHelper.show_success(self.tr(msg_key))

    def _check_subscription_change_instant(self):
        try:
            from android_utils import run_on_ui_thread
            cur_sub = self.is_subscribed()
            prev_sub = self._last_known_sub
            if prev_sub is not None and prev_sub != cur_sub:
                self._last_known_sub = cur_sub
                if cur_sub:
                    was_exp = bool(self.plugin.get_setting("was_expired_state", False))
                    self.plugin.set_setting("was_subscribed", True)
                    self.plugin.set_setting("was_expired_state", False)
                    self._dismiss_active_dialog()
                    self._notify_subscribed(was_expired=was_exp)
                else:
                    self.plugin.set_setting("was_subscribed", False)
                    self.plugin.set_setting("was_expired_state", True)
                    self._dismiss_active_dialog()
                    self._show_expired_card()
            else:
                self._last_known_sub = cur_sub
        except Exception:
            pass

    def _start_subscription_watcher(self):
        def watcher_loop():
            import time
            from android_utils import run_on_ui_thread
            time.sleep(2.0)
            if self._stopped: return
            self._last_known_sub = self.is_subscribed()

            while not self._stopped:
                try:
                    time.sleep(2.0)
                    if self._stopped: break
                    cur_sub = self.is_subscribed()
                    prev_sub = self._last_known_sub

                    if prev_sub is not None and prev_sub != cur_sub:
                        self._last_known_sub = cur_sub
                        if cur_sub:
                            was_exp = bool(self.plugin.get_setting("was_expired_state", False))
                            self.plugin.set_setting("was_subscribed", True)
                            self.plugin.set_setting("was_expired_state", False)
                            def on_sub():
                                self._dismiss_active_dialog()
                                self._notify_subscribed(was_expired=was_exp)
                            run_on_ui_thread(on_sub)
                        else:
                            self.plugin.set_setting("was_subscribed", False)
                            self.plugin.set_setting("was_expired_state", True)
                            def on_unsub():
                                self._dismiss_active_dialog()
                                self._show_expired_card()
                            run_on_ui_thread(on_unsub)
                    else:
                        self._last_known_sub = cur_sub
                except Exception:
                    pass
        import threading
        t = threading.Thread(target=watcher_loop, daemon=True)
        t.start()

    def _check_initial_subscription_flow(self):
        def flow():
            import time
            try:
                now = time.time()
                install_time = float(self.plugin.get_setting("install_time", 0.0) or 0.0)
                if install_time <= 0:
                    install_time = now
                    self.plugin.set_setting("install_time", install_time)

                is_sub = self.is_subscribed()
                self._last_known_sub = is_sub
                was_sub = bool(self.plugin.get_setting("was_subscribed", False))
                was_exp = bool(self.plugin.get_setting("was_expired_state", False))

                if is_sub:
                    if not was_sub:
                        self.plugin.set_setting("was_subscribed", True)
                        self.plugin.set_setting("was_expired_state", False)
                        self._notify_subscribed(was_expired=was_exp)
                    return

                if was_sub or was_exp:
                    self.plugin.set_setting("was_subscribed", False)
                    self.plugin.set_setting("was_expired_state", True)
                    self._show_expired_card()
                    return

                trial_days = float(self._get_links().get("trial_days", 7))
                trial_sec = trial_days * 86400.0
                last_remind = float(self.plugin.get_setting("last_remind_time", 0.0) or 0.0)

                if (now - install_time) < trial_sec:
                    if last_remind <= 0 or (now - last_remind) >= trial_sec:
                        self._show_welcome_card()
                else:
                    self.plugin.set_setting("was_expired_state", True)
                    self._show_expired_card()
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(flow, 1500)

    def _create_card_content(self, act, title_text, message_text):
        try:
            from android.widget import LinearLayout, TextView
            from android.view import Gravity
            from android.graphics import Typeface
            from org.telegram.messenger import AndroidUtilities, UserConfig, MediaDataController
            from org.telegram.ui.ActionBar import Theme
            from org.telegram.ui.Components import BackupImageView

            dp = AndroidUtilities.dp
            layout = LinearLayout(act)
            layout.setOrientation(LinearLayout.VERTICAL)
            layout.setGravity(Gravity.CENTER_HORIZONTAL)
            layout.setPadding(dp(24), dp(16), dp(24), dp(6))

            size = dp(76)
            iv = BackupImageView(act)
            iv.setRoundRadius(dp(18))
            lp_iv = LinearLayout.LayoutParams(size, size)
            lp_iv.gravity = Gravity.CENTER_HORIZONTAL
            lp_iv.setMargins(0, dp(4), 0, dp(16))
            iv.setLayoutParams(lp_iv)

            try:
                acc = int(UserConfig.selectedAccount)
                mdc = MediaDataController.getInstance(acc)
                raw_icon = str(self.icon_sticker)
                if "/" in raw_icon:
                    pack, idx_s = raw_icon.split("/", 1)
                    mdc.setPlaceholderImageByIndex(iv, pack, int(idx_s), "150_150")
            except Exception:
                pass
            layout.addView(iv)

            if title_text:
                tt = TextView(act)
                tt.setText(title_text)
                tt.setTextSize(19.0)
                tt.setTypeface(None, Typeface.BOLD)
                tt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                tt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_t = LinearLayout.LayoutParams(-1, -2)
                lp_t.setMargins(0, 0, 0, dp(12))
                tt.setLayoutParams(lp_t)
                layout.addView(tt)

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

    def _show_welcome_card(self):
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

                def on_sub(d, w):
                    self._dismiss_active_dialog()
                    self._open_url(url)
                    def check_after():
                        if self.is_subscribed():
                            self._notify_subscribed(was_expired=False)
                    from android_utils import run_on_ui_thread
                    run_on_ui_thread(check_after, 3000)

                def on_later(d, w):
                    import time
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("last_remind_time", time.time())

                b.set_positive_button(self.tr("subscribe_btn"), on_sub)
                b.set_negative_button(self.tr("remind_later_btn"), on_later)
                self._active_dialog = b.show()
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(show)

    def _show_expired_card(self):
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
                from ui.bulletin import BulletinHelper
                b = AlertDialogBuilder(act)
                content = self._create_card_content(act, self.tr("trial_expired_title"), self.tr("trial_expired_msg"))
                if content is not None:
                    b.set_view(content)
                else:
                    b.set_title(self.tr("trial_expired_title"))
                    b.set_message(self.tr("trial_expired_msg"))

                def on_sub(d, w):
                    self._dismiss_active_dialog()
                    self._open_url(url)
                    def check_after():
                        if self.is_subscribed():
                            self.plugin.set_setting("was_subscribed", True)
                            self.plugin.set_setting("was_expired_state", False)
                            self._notify_subscribed(was_expired=True)
                    from android_utils import run_on_ui_thread
                    run_on_ui_thread(check_after, 3000)

                def on_check(d, w):
                    self._dismiss_active_dialog()
                    if self.is_subscribed():
                        self.plugin.set_setting("was_subscribed", True)
                        self.plugin.set_setting("was_expired_state", False)
                        self._notify_subscribed(was_expired=True)
                    else:
                        BulletinHelper.show_error(self.tr("sub_not_found"))
                        from android_utils import run_on_ui_thread
                        run_on_ui_thread(self._show_expired_card, 500)

                b.set_positive_button(self.tr("subscribe_btn"), on_sub)
                b.set_negative_button(self.tr("check_sub_btn"), on_check)
                self._active_dialog = b.show()
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(show)


class TempSubPlugin(BasePlugin):

    def on_plugin_load(self):
        self._author_helper = AuthorChannelHelper(self, icon_sticker=globals().get("__icon__", "MaxExteraPluginsStickers/1"))
        self._author_helper.on_load()
        self._ensure_data()
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
                        run_on_ui_thread(lambda: me._try_attach_button(chat_activity), 300)
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
            from android.widget import ImageView
            from android.graphics.drawable import GradientDrawable
            from org.telegram.ui.ActionBar import Theme
            from org.telegram.ui.Components import LayoutHelper
            from java import jclass

            try:
                activity = container.getContext()
            except:
                activity = _activity()
            if activity is None:
                return None

            button = ImageView(activity)

            bg = GradientDrawable()
            try:
                bg.setColor(Theme.getColor(Theme.key_featuredStickers_buttonText))
            except:
                bg.setColor(-1)
            try:
                bg.setCornerRadius(AndroidUtilities.dp(21))
            except:
                pass
            button.setBackground(bg)

            try:
                icon_cls = jclass("org.telegram.messenger.R$drawable")
                icon_id = getattr(icon_cls, "msg_calendar2", 0)
            except:
                icon_id = 0
            if icon_id:
                try:
                    button.setImageResource(icon_id)
                except:
                    icon_id = 0
            if icon_id:
                try:
                    accent = Theme.getColor(Theme.key_featuredStickers_addButton)
                    button.setColorFilter(accent)
                except:
                    pass

            try:
                button.setContentDescription("Временная подписка")
            except:
                pass

            button.setTag(BUTTON_TAG)
            button.setScaleType(ImageView.ScaleType.CENTER)
            button.setClickable(True)

            lp = LayoutHelper.createFrame(40, 40, 19, 4, 2, 0, 2)
            container.addView(button, lp)
            return button
        except Exception:
            _log_err(self, "temp_sub: create button error")
            return None

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

            try:
                if visible:
                    try:
                        if container.getVisibility() == 0:
                            button.setVisibility(0)
                        else:
                            button.setVisibility(4)
                    except:
                        button.setVisibility(0)
                else:
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
                run_on_ui_thread(lambda: me._show_add_dialog(cid))
            elif which == 1:
                me._remove_subscription(cid, also_leave=True)

        builder = AlertDialogBuilder(a)
        builder.set_title(title)
        builder.set_items(
            ["Редактировать", "Отписаться и удалить"],
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

        subs = me._get_subs()
        if subs:
            items.append(Divider())
            items.append(Header(text=f"Активные подписки ({len(subs)})"))
            for sub in subs:
                dt = datetime.datetime.fromtimestamp(sub.get("leave_at", 0))
                date_str = dt.strftime("%d.%m.%Y %H:%M")
                title = sub.get("title", sub.get("username", "???"))
                ch_id = sub.get("channel_id", 0)

                def make_click_handler(cid):
                    return lambda v: run_on_ui_thread(lambda: me._show_item_actions(cid))

                items.append(
                    Text(
                        text=f"📢 {title}",
                        subtext=f"Отписка: {date_str}",
                        icon="msg_channel",
                        on_click=make_click_handler(ch_id),
                    )
                )
            items.append(
                Text(
                    text="Нажмите на подписку для действий",
                    icon="msg_info",
                )
            )
        else:
            items.append(Divider())
            items.append(
                Text(
                    text="Нет подписок. Добавьте первую!",
                    icon="msg_info",
                )
            )

        if hasattr(self, "_author_helper"):


            return self._author_helper.prepend_settings(items)


        return items

# __DEX_BEGIN__
# __DEX_END__
