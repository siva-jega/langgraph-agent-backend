"""Facebook Messenger channel provider — placeholder.

To implement:
  1. Create ``adapter.py`` in this package with a ``MessengerAdapter`` class.
  2. The provider should subclass ``ChannelProvider`` from ``..base``.
  3. Implement ``get_prompt_overlay``, ``format_response``, and ``send``.
  4. Register it in ``providers/__init__.py``.

Typical responsibilities:
  - Prompt overlay: conversational tone, structured messages support
  - Format: plain text or structured message templates (buttons, quick replies)
  - Send: call Meta Send API ``POST /v19.0/me/messages``
"""
