"""Local patches over the upstream Amazon Music API submodule. Subclasses the read-only `AmazonMusicMobileAPI` to fix two login touch points — forwarding the OAuth callback through the JP Prime Video recursion, and silencing the upstream login banner that desyncs the UI redraw."""

import contextvars
import typing

import amazonmusic.azapi as _az
from amazonmusic.azapi import AmazonMusicMobileAPI as _BaseAPI

_oauth_callback: "contextvars.ContextVar[typing.Optional[typing.Callable[[str, str], str]]]" = (
    contextvars.ContextVar("amzn_oauth_flow_callback", default=None)
)

_LOGIN_BANNER_PREFIX = "Login confirmed for "


def _make_filtered_print(real_print):
    printer = real_print or print

    def _filtered_print(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith(_LOGIN_BANNER_PREFIX):
            return
        return printer(*args, **kwargs)

    return _filtered_print


class AmazonMusicMobileAPI(_BaseAPI):

    @classmethod
    def login_via_mobile(cls, *args, oauth_flow_callback=None, **kwargs):
        token = (
            _oauth_callback.set(oauth_flow_callback)
            if oauth_flow_callback is not None
            else None
        )
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
        if oauth_flow_callback is None:
            oauth_flow_callback = _oauth_callback.get()
        return _BaseAPI._exteral_login(oauth_url, application, oauth_flow_callback)
