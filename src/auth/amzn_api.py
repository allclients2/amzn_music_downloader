"""Local patches over the upstream Amazon Music API submodule.

`src/amazonmusic/` is an unmodified git submodule of OrpheusDL's Amazon Music
client and is treated as read-only. The CLI/bot need two small fixes to its login
flow that upstream lacks, so rather than fork the ~2k-line `azapi.py` we subclass
it here and override just the two touch points:

1. **JP login callback forwarding.** For the JP marketplace `login_via_mobile`
   signs into Prime Video first (an Amazon quirk) via a recursive self-call that
   upstream makes *without* passing `oauth_flow_callback` along. The browser-OAuth
   handler is therefore lost on that inner call and `_exteral_login` falls back to
   reading the (very long) callback URL from terminal stdin — which crashes in a
   headless/GUI context. We stash the callback in a `ContextVar` so it survives the
   recursion and recover it in an overridden `_exteral_login`.

2. **Silenced login banner.** Upstream prints a `Login confirmed for …` line at the
   end of `login_via_mobile`. That stray stdout write desyncs `src/ui.py`'s
   one-screen-at-a-time redraw (`auth.login` surfaces its own confirmation), so we
   drop just that line by filtering the submodule module's `print` for the duration
   of the login call. Everything else it prints passes through untouched.
"""

import contextvars
import typing

import amazonmusic.azapi as _az
from amazonmusic.azapi import AmazonMusicMobileAPI as _BaseAPI

# Browser-OAuth callback, stashed so it survives login_via_mobile's JP Prime-Video
# pre-login recursion (which upstream does not forward it through).
_oauth_callback: "contextvars.ContextVar[typing.Optional[typing.Callable[[str, str], str]]]" = (
    contextvars.ContextVar("amzn_oauth_flow_callback", default=None)
)

_LOGIN_BANNER_PREFIX = "Login confirmed for "


def _make_filtered_print(real_print):
    """A `print` shim that swallows the upstream `Login confirmed for …` banner and
    forwards everything else to the real `print`."""
    printer = real_print or print

    def _filtered_print(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith(_LOGIN_BANNER_PREFIX):
            return
        return printer(*args, **kwargs)

    return _filtered_print


class AmazonMusicMobileAPI(_BaseAPI):
    """`amazonmusic.azapi.AmazonMusicMobileAPI` with the two CLI/bot login fixes."""

    @classmethod
    def login_via_mobile(cls, *args, oauth_flow_callback=None, **kwargs):
        # Publish the callback for the JP recursion's _exteral_login to recover.
        # Only set it when present so the inner recursive call (which passes None)
        # doesn't clobber the outer value.
        token = (
            _oauth_callback.set(oauth_flow_callback)
            if oauth_flow_callback is not None
            else None
        )
        # Drop the "Login confirmed for …" banner for the duration of login by
        # temporarily filtering the submodule module's `print` (restored below).
        real_print = _az.__dict__.get("print")
        _az.print = _make_filtered_print(real_print)
        try:
            return super().login_via_mobile(
                *args, oauth_flow_callback=oauth_flow_callback, **kwargs
            )
        finally:
            if real_print is None:
                _az.__dict__.pop("print", None)
            else:
                _az.print = real_print
            if token is not None:
                _oauth_callback.reset(token)

    @staticmethod
    def _exteral_login(oauth_url, application, oauth_flow_callback=None):
        # Upstream drops the callback through the JP pre-login recursion; recover it
        # here so the inner login uses the same browser flow as the outer one.
        if oauth_flow_callback is None:
            oauth_flow_callback = _oauth_callback.get()
        return _BaseAPI._exteral_login(oauth_url, application, oauth_flow_callback)
