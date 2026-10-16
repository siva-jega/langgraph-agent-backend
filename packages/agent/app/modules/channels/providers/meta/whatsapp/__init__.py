"""WhatsApp channel provider — placeholder.

To implement:
  1. Create ``adapter.py`` in this package with a ``WhatsAppAdapter`` class.
  2. The provider should subclass ``ChannelProvider`` from ``..base``.
  3. Implement ``get_prompt_overlay``, ``format_response``, and ``send``.
  4. Register it in ``providers/__init__.py``.

Typical responsibilities:
  - Prompt overlay: conversational tone, emoji-friendly, short messages
  - Format: plain text (WhatsApp supports limited formatting: *bold*, _italic_)
  - Send: call Meta Cloud API ``POST /v19.0/{phone_number_id}/messages``
"""
