"""Instagram channel provider — placeholder.

To implement:
  1. Create ``adapter.py`` in this package with an ``InstagramAdapter`` class.
  2. The provider should subclass ``ChannelProvider`` from ``..base``.
  3. Implement ``get_prompt_overlay``, ``format_response``, and ``send``.
  4. Register it in ``providers/__init__.py``.

Typical responsibilities:
  - Prompt overlay: casual/friendly tone, short messages, emoji-aware
  - Format: plain text (Instagram DM supports limited formatting)
  - Send: call Instagram Messaging API via Meta Graph API
"""
