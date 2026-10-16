"""Unit tests for Aide field mapping (no DB, no LangGraph)."""

from __future__ import annotations

from common.tools.providers.aide.field_mapping import (
    map_submitted_to_customer,
    map_submitted_to_lead,
    map_submitted_to_ticket,
    normalize_ticket_priority,
    validate_form_field_values,
    validate_required_form_fields,
)


class TestNormalizeTicketPriority:
    def test_valid_uppercase(self) -> None:
        assert normalize_ticket_priority("high") == "HIGH"
        assert normalize_ticket_priority("LOW") == "LOW"

    def test_invalid_falls_back(self) -> None:
        assert normalize_ticket_priority("nope") == "MEDIUM"
        assert normalize_ticket_priority(None) == "MEDIUM"


class TestValidateRequiredFormFields:
    def test_ok_when_no_required(self) -> None:
        fields = [{"id": "x", "label": "X", "required": False}]
        ok, err = validate_required_form_fields(fields, {})
        assert ok and err == ""

    def test_missing_required(self) -> None:
        fields = [{"id": "phone", "label": "Phone", "required": True}]
        ok, err = validate_required_form_fields(fields, {})
        assert not ok
        assert "Phone" in err

    def test_present_required(self) -> None:
        fields = [{"id": "phone", "label": "Phone", "required": True}]
        ok, err = validate_required_form_fields(fields, {"phone": "  123  "})
        assert ok and err == ""

    def test_required_media_present_as_list(self) -> None:
        fields = [{"id": "att", "label": "Attachment", "type": "media_upload", "required": True}]
        ok, err = validate_required_form_fields(
            fields, {"att": [{"key": "tickets/x.png", "url": "", "name": "x.png"}]}
        )
        assert ok and err == ""

    def test_required_media_empty_list_fails(self) -> None:
        fields = [{"id": "att", "label": "Attachment", "type": "media_upload", "required": True}]
        ok, err = validate_required_form_fields(fields, {"att": []})
        assert not ok
        assert "Attachment" in err


class TestValidateFormFieldValues:
    def test_valid_email_and_phone(self) -> None:
        fields = [
            {"id": "email", "label": "Email", "type": "email"},
            {"id": "phone", "label": "Phone", "type": "phone"},
        ]
        ok, err = validate_form_field_values(
            fields, {"email": " a@b.co ", "phone": "+1 (555) 123-4567"}
        )
        assert ok and err == ""

    def test_invalid_email(self) -> None:
        fields = [{"id": "email", "label": "Mail", "type": "email"}]
        ok, err = validate_form_field_values(fields, {"email": "not-an-email"})
        assert not ok
        assert "Mail" in err

    def test_invalid_phone(self) -> None:
        fields = [{"id": "phone", "label": "Phone", "type": "phone"}]
        ok, err = validate_form_field_values(fields, {"phone": "12"})
        assert not ok
        assert "Phone" in err

    def test_phone_with_trailing_letters_rejected(self) -> None:
        fields = [{"id": "phone", "label": "Phone", "type": "phone"}]
        ok, err = validate_form_field_values(
            fields, {"phone": "+91 9179822431iuguugugguygg"}
        )
        assert not ok
        assert "Phone" in err

    def test_empty_optional_typed_field_skipped(self) -> None:
        fields = [{"id": "email", "label": "Email", "type": "email"}]
        ok, err = validate_form_field_values(fields, {"email": ""})
        assert ok and err == ""

    def test_non_typed_field_not_validated(self) -> None:
        fields = [{"id": "note", "label": "Note", "type": "text"}]
        ok, err = validate_form_field_values(fields, {"note": "anything @ goes"})
        assert ok and err == ""

    def test_type_case_insensitive(self) -> None:
        fields = [{"id": "email", "label": "Email", "type": "Email"}]
        ok, err = validate_form_field_values(fields, {"email": "bad"})
        assert not ok


class TestMapSubmittedToTicket:
    def test_maps_to_requester_and_priority(self) -> None:
        form_fields = [
            {
                "id": "n",
                "mapsTo": "ticket.requesterName",
            },
            {
                "id": "e",
                "mapsTo": "ticket.requesterEmail",
            },
            {
                "id": "p",
                "mapsTo": "ticket.priority",
            },
        ]
        submitted = {"n": "Ann", "e": "a@b.co", "p": "urgent"}
        m = map_submitted_to_ticket(form_fields, submitted)
        assert m.data["requesterName"] == "Ann"
        assert m.data["requesterEmail"] == "a@b.co"
        assert m.data["priority"] == "URGENT"
        assert m.data == {
            "requesterName": "Ann",
            "requesterEmail": "a@b.co",
            "priority": "URGENT",
        }

    def test_custom_dot_prefix(self) -> None:
        form_fields = [
            {"id": "f1", "mapsTo": "custom.phone"},
        ]
        m = map_submitted_to_ticket(form_fields, {"f1": "+1555"})
        assert m.data == {"phone": "+1555"}

    def test_empty_maps_to_goes_to_custom_by_field_id(self) -> None:
        form_fields = [{"id": "ref", "mapsTo": ""}]
        m = map_submitted_to_ticket(form_fields, {"ref": "ABC"})
        assert m.data == {"ref": "ABC"}

    def test_unknown_ticket_path_uses_maps_to_suffix(self) -> None:
        """Unknown ticket.* paths still yield a key: last segment after the prefix."""
        form_fields = [{"id": "weird", "mapsTo": "ticket.unknownColumn"}]
        m = map_submitted_to_ticket(form_fields, {"weird": "val"})
        assert m.data == {"unknownColumn": "val"}

    def test_skips_empty_values(self) -> None:
        form_fields = [{"id": "a", "mapsTo": "ticket.requesterName"}]
        m = map_submitted_to_ticket(form_fields, {"a": "  "})
        assert m.data == {}

    def test_media_list_preserved_as_structured(self) -> None:
        refs = [{"key": "tickets/x.png", "url": "", "name": "x.png", "size": 12, "mimeType": "image/png"}]
        form_fields = [{"id": "att", "type": "media_upload", "mapsTo": "custom.attachments"}]
        m = map_submitted_to_ticket(form_fields, {"att": refs})
        assert m.data == {"attachments": refs}

    def test_non_media_list_is_string_coerced(self) -> None:
        form_fields = [{"id": "note", "type": "text", "mapsTo": "custom.note"}]
        m = map_submitted_to_ticket(form_fields, {"note": [{"injected": 1}]})
        assert m.data == {"note": str([{"injected": 1}])}
        assert isinstance(m.data["note"], str)


class TestMapSubmittedToCustomer:
    """Tests for the new customer.* mapsTo paths."""

    def test_maps_canonical_customer_paths(self) -> None:
        form_fields = [
            {"id": "n", "mapsTo": "customer.name"},
            {"id": "em", "mapsTo": "customer.customerEmail"},
            {"id": "co", "mapsTo": "customer.companyName"},
            {"id": "job", "mapsTo": "customer.designation"},
        ]
        submitted = {"n": "Bob", "em": "b@x.co", "co": "Acme", "job": "CTO"}
        m = map_submitted_to_customer(form_fields, submitted)
        assert m.data["name"] == "Bob"
        assert m.data["email"] == "b@x.co"
        assert m.data["companyName"] == "Acme"
        assert m.data["designation"] == "CTO"
        assert m.data == {
            "name": "Bob",
            "email": "b@x.co",
            "companyName": "Acme",
            "designation": "CTO",
        }

    def test_legacy_lead_paths_still_work(self) -> None:
        form_fields = [
            {"id": "n", "mapsTo": "lead.name"},
            {"id": "em", "mapsTo": "lead.leadEmail"},
            {"id": "co", "mapsTo": "lead.companyName"},
            {"id": "job", "mapsTo": "lead.designation"},
        ]
        submitted = {"n": "Bob", "em": "b@x.co", "co": "Acme", "job": "CTO"}
        m = map_submitted_to_customer(form_fields, submitted)
        assert m.data["name"] == "Bob"
        assert m.data["email"] == "b@x.co"
        assert m.data["companyName"] == "Acme"
        assert m.data["designation"] == "CTO"

    def test_custom_field(self) -> None:
        form_fields = [{"id": "x", "mapsTo": "custom.source"}]
        m = map_submitted_to_customer(form_fields, {"x": "widget"})
        assert m.data == {"source": "widget"}

    def test_backward_compat_alias(self) -> None:
        """map_submitted_to_lead still works as an alias."""
        form_fields = [{"id": "n", "mapsTo": "customer.name"}]
        m = map_submitted_to_lead(form_fields, {"n": "Alice"})
        assert m.data["name"] == "Alice"
