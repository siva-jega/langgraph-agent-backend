from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "list_airtable_records": {
        "id": "list_airtable_records",
        "name": "List Airtable Records",
        "description": "List Airtable records from a table with optional filtering, sorting, and pagination.",
    },
    "get_airtable_record": {
        "id": "get_airtable_record",
        "name": "Get Airtable Record",
        "description": "Fetch a single Airtable record by its record id.",
    },
    "update_airtable_record": {
        "id": "update_airtable_record",
        "name": "Update Airtable Record",
        "description": "Update fields on a specific Airtable record.",
    },
    "update_multiple_airtable_records": {
        "id": "update_multiple_airtable_records",
        "name": "Update Multiple Airtable Records",
        "description": "Update many Airtable records in one request payload.",
    },
    "create_airtable_record": {
        "id": "create_airtable_record",
        "name": "Create Airtable Record",
        "description": "Create a new Airtable record in a table with the provided fields.",
    },
    "upload_airtable_attachment": {
        "id": "upload_airtable_attachment",
        "name": "Upload Airtable Attachment",
        "description": "Upload a file attachment to an Airtable record.",
    },
    "update_airtable_field": {
        "id": "update_airtable_field",
        "name": "Update Airtable Field",
        "description": "Update an Airtable field (e.g., configuration) for the specified table.",
    },
    "create_airtable_field": {
        "id": "create_airtable_field",
        "name": "Create Airtable Field",
        "description": "Create a new field in an Airtable table.",
    },
    "update_airtable_table": {
        "id": "update_airtable_table",
        "name": "Update Airtable Table",
        "description": "Update Airtable table configuration in the specified base.",
    },
    "create_airtable_table": {
        "id": "create_airtable_table",
        "name": "Create Airtable Table",
        "description": "Create a new Airtable table in the specified base.",
    },
    "list_airtable_bases": {
        "id": "list_airtable_bases",
        "name": "List Airtable Bases",
        "description": "List all Airtable bases the connected user has access to.",
    },
    "get_airtable_base_schema": {
        "id": "get_airtable_base_schema",
        "name": "Get Airtable Base Schema",
        "description": "Fetch the schema for an Airtable base, including tables and fields.",
    },
    "create_airtable_base": {
        "id": "create_airtable_base",
        "name": "Create Airtable Base",
        "description": "Create a new Airtable base for the connected user/workspace.",
    },
    "get_airtable_user_info": {
        "id": "get_airtable_user_info",
        "name": "Get Airtable User Info",
        "description": "Get information about the connected Airtable user and available scopes.",
    },
}

__all__ = ["TOOLS_DATA"]

