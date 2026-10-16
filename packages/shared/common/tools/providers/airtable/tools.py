"""Airtable tools for managing records, fields, tables, and bases."""

import base64
from pathlib import Path
from typing import List, Optional, Dict, Any
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import (
    AirtableRecord,
    AirtableField,
    AirtableTable,
    AirtableBase,
    ListRecordsResult,
    GetRecordResult,
    UpdateRecordResult,
    UpdateMultipleRecordsResult,
    CreateRecordResult,
    UploadAttachmentResult,
    UpdateFieldResult,
    CreateFieldResult,
    UpdateTableResult,
    CreateTableResult,
    ListBasesResult,
    GetBaseSchemaResult,
    CreateBaseResult,
    GetUserInfoResult,
)
from .utils import get_airtable_credentials, make_airtable_request
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)

BASE_URL = "https://api.airtable.com/v0"
META_BASE_URL = "https://api.airtable.com/v0/meta"


@tool
async def list_airtable_records(
    base_id: str,
    table_id: str,
    fields: Optional[List[str]] = None,
    filter_by_formula: Optional[str] = None,
    max_records: Optional[int] = None,
    page_size: Optional[int] = None,
    sort: Optional[List[Dict[str, str]]] = None,
    view: Optional[str] = None,
    cell_format: Optional[str] = None,
    time_zone: Optional[str] = None,
    user_locale: Optional[str] = None,
    offset: Optional[str] = None,
    config: RunnableConfig = None,
) -> ListRecordsResult:
    """
    List records from an Airtable table with optional filtering, sorting, and pagination.

    Args:
        base_id: The base ID
        table_id: The table ID or name
        fields: List of field names to include
        filter_by_formula: Airtable formula to filter records
        max_records: Maximum number of records to return
        page_size: Number of records per page (max 100)
        sort: List of sort objects with field and direction
        view: Name or ID of a view
        cell_format: json (default) or string
        time_zone: Time zone for datetime fields
        user_locale: Locale for formatting
        offset: Pagination offset
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{BASE_URL}/{base_id}/{table_id}"
        params = {}

        if fields:
            for field in fields:
                params[f"fields[]"] = field
        if filter_by_formula:
            params["filterByFormula"] = filter_by_formula
        if max_records:
            params["maxRecords"] = max_records
        if page_size:
            params["pageSize"] = min(page_size, 100)
        if sort:
            for i, sort_item in enumerate(sort):
                params[f"sort[{i}][field]"] = sort_item["field"]
                params[f"sort[{i}][direction]"] = sort_item.get("direction", "asc")
        if view:
            params["view"] = view
        if cell_format:
            params["cellFormat"] = cell_format
        if time_zone:
            params["timeZone"] = time_zone
        if user_locale:
            params["userLocale"] = user_locale
        if offset:
            params["offset"] = offset

        response = await make_airtable_request(
            "GET", url, credentials.token, params=params
        )
        records = [
            AirtableRecord.model_validate(record)
            for record in response.get("records", [])
        ]
        return {
            "records": records,
            "offset": response.get("offset"),
        }
    except Exception as e:
        logger.error(f"Error listing records: {str(e)}")
        return {"records": [], "offset": None, "error": str(e)}


@tool
async def get_airtable_record(
    base_id: str,
    table_id: str,
    record_id: str,
    cell_format: Optional[str] = None,
    time_zone: Optional[str] = None,
    user_locale: Optional[str] = None,
    config: RunnableConfig = None,
) -> GetRecordResult:
    """
    Get a specific record from an Airtable table by its ID.

    Args:
        base_id: The base ID
        table_id: The table ID or name
        record_id: The record ID
        cell_format: json (default) or string
        time_zone: Time zone for datetime fields
        user_locale: Locale for formatting
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{BASE_URL}/{base_id}/{table_id}/{record_id}"
        params = {}

        if cell_format:
            params["cellFormat"] = cell_format
        if time_zone:
            params["timeZone"] = time_zone
        if user_locale:
            params["userLocale"] = user_locale

        response = await make_airtable_request(
            "GET", url, credentials.token, params=params
        )
        record = AirtableRecord.model_validate(response)
        return {"record": record}
    except Exception as e:
        logger.error(f"Error getting record: {str(e)}")
        return {"error": str(e)}


@tool
async def update_airtable_record(
    base_id: str,
    table_id: str,
    record_id: str,
    fields: Dict[str, Any],
    destructive_update: bool = False,
    config: RunnableConfig = None,
) -> UpdateRecordResult:
    """
    Update a specific record in an Airtable table.

    Args:
        base_id: The base ID
        table_id: The table ID or name
        record_id: The record ID
        fields: Dictionary of field values to update
        destructive_update: If True, clear fields not included in the request
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{BASE_URL}/{base_id}/{table_id}/{record_id}"
        data = {"fields": fields}

        method = "PUT" if destructive_update else "PATCH"
        response = await make_airtable_request(
            method, url, credentials.token, data=data
        )
        record = AirtableRecord.model_validate(response)
        return {"record": record}
    except Exception as e:
        logger.error(f"Error updating record: {str(e)}")
        return {"error": str(e)}


@tool
async def update_multiple_airtable_records(
    base_id: str,
    table_id: str,
    records: List[Dict[str, Any]],
    destructive_update: bool = False,
    config: RunnableConfig = None,
) -> UpdateMultipleRecordsResult:
    """
    Update multiple records in an Airtable table at once.

    Args:
        base_id: The base ID
        table_id: The table ID or name
        records: List of record objects with id and fields
        destructive_update: If True, clear fields not included in the request
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{BASE_URL}/{base_id}/{table_id}"
        data = {"records": records}

        method = "PUT" if destructive_update else "PATCH"
        response = await make_airtable_request(
            method, url, credentials.token, data=data
        )
        updated_records = [
            AirtableRecord.model_validate(record)
            for record in response.get("records", [])
        ]
        return {"records": updated_records}
    except Exception as e:
        logger.error(f"Error updating multiple records: {str(e)}")
        return {"records": [], "error": str(e)}


@tool
async def create_airtable_record(
    base_id: str,
    table_id: str,
    fields: Dict[str, Any],
    config: RunnableConfig = None,
) -> CreateRecordResult:
    """
    Create a new record in an Airtable table.

    Args:
        base_id: The base ID
        table_id: The table ID or name
        fields: Dictionary of field values for the new record
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{BASE_URL}/{base_id}/{table_id}"
        data = {"fields": fields}

        response = await make_airtable_request(
            "POST", url, credentials.token, data=data
        )
        record = AirtableRecord.model_validate(response)
        return {"record": record}
    except Exception as e:
        logger.error(f"Error creating record: {str(e)}")
        return {"error": str(e)}


@tool
async def upload_airtable_attachment(
    base_id: str,
    table_id: str,
    record_id: str,
    field_name: str,
    file_path: str,
    filename: Optional[str] = None,
    config: RunnableConfig = None,
) -> UploadAttachmentResult:
    """
    Upload an attachment to an Airtable record.

    Args:
        base_id: The base ID
        table_id: The table ID or name
        record_id: The record ID
        field_name: The attachment field name
        file_path: Path to the file to upload
        filename: Optional custom filename
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {"error": f"File not found: {file_path}"}

        with open(file_path_obj, "rb") as f:
            file_content = f.read()

        attachment = {
            "url": f"data:application/octet-stream;base64,{base64.b64encode(file_content).decode()}",
            "filename": filename or file_path_obj.name,
        }

        url = f"{BASE_URL}/{base_id}/{table_id}/{record_id}"
        data = {"fields": {field_name: [attachment]}}

        await make_airtable_request("PATCH", url, credentials.token, data=data)
        return {"attachment": attachment}
    except Exception as e:
        logger.error(f"Error uploading attachment: {str(e)}")
        return {"error": str(e)}


@tool
async def update_airtable_field(
    base_id: str,
    table_id: str,
    field_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    config: RunnableConfig = None,
) -> UpdateFieldResult:
    """
    Update a field in an Airtable table.

    Args:
        base_id: The base ID
        table_id: The table ID
        field_id: The field ID
        name: New field name
        description: New field description
        options: New field options
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "airtable" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_airtable_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases/{base_id}/tables/{table_id}/fields/{field_id}"
        data = {}

        if name:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if options:
            data["options"] = options

        response = await make_airtable_request(
            "PATCH", url, credentials.token, data=data
        )
        field = AirtableField.model_validate(response)
        return {"field": field}
    except Exception as e:
        logger.error(f"Error updating field: {str(e)}")
        return {"error": str(e)}


@tool
async def create_airtable_field(
    base_id: str,
    table_id: str,
    name: str,
    field_type: str,
    description: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    config: RunnableConfig = None,
) -> CreateFieldResult:
    """
    Create a new field in an Airtable table.

    Args:
        base_id: The base ID
        table_id: The table ID
        name: Field name
        field_type: Field type (singleLineText, multilineText, number, etc.)
        description: Field description
        options: Field options specific to the field type
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases/{base_id}/tables/{table_id}/fields"
        data = {"name": name, "type": field_type}

        if description:
            data["description"] = description
        if options:
            data["options"] = options

        response = await make_airtable_request(
            "POST", url, credentials.token, data=data
        )
        field = AirtableField.model_validate(response)
        return {"field": field}
    except Exception as e:
        logger.error(f"Error creating field: {str(e)}")
        return {"error": str(e)}


@tool
async def update_airtable_table(
    base_id: str,
    table_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    config: RunnableConfig = None,
) -> UpdateTableResult:
    """
    Update a table in an Airtable base.

    Args:
        base_id: The base ID
        table_id: The table ID
        name: New table name
        description: New table description
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases/{base_id}/tables/{table_id}"
        data = {}

        if name:
            data["name"] = name
        if description is not None:
            data["description"] = description

        response = await make_airtable_request(
            "PATCH", url, credentials.token, data=data
        )
        table = AirtableTable.model_validate(response)
        return {"table": table}
    except Exception as e:
        logger.error(f"Error updating table: {str(e)}")
        return {"error": str(e)}


@tool
async def create_airtable_table(
    base_id: str,
    name: str,
    description: Optional[str] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
    config: RunnableConfig = None,
) -> CreateTableResult:
    """
    Create a new table in an Airtable base.

    Args:
        base_id: The base ID
        name: Table name
        description: Table description
        fields: List of field definitions
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases/{base_id}/tables"
        data = {"name": name}

        if description:
            data["description"] = description
        if fields:
            data["fields"] = fields

        response = await make_airtable_request(
            "POST", url, credentials.token, data=data
        )
        table = AirtableTable.model_validate(response)
        return {"table": table}
    except Exception as e:
        logger.error(f"Error creating table: {str(e)}")
        return {"error": str(e)}


@tool
async def list_airtable_bases(
    offset: Optional[str] = None,
    config: RunnableConfig = None,
) -> ListBasesResult:
    """
    List all accessible Airtable bases.

    Args:
        offset: Pagination offset
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases"
        params = {}

        if offset:
            params["offset"] = offset

        response = await make_airtable_request(
            "GET", url, credentials.token, params=params
        )
        bases = [
            AirtableBase.model_validate(base) for base in response.get("bases", [])
        ]
        return {
            "bases": bases,
            "offset": response.get("offset"),
        }
    except Exception as e:
        logger.error(f"Error listing bases: {str(e)}")
        return {"bases": [], "offset": None, "error": str(e)}


@tool
async def get_airtable_base_schema(
    base_id: str,
    config: RunnableConfig = None,
) -> GetBaseSchemaResult:
    """
    Get the complete schema of an Airtable base including all tables and fields.

    Args:
        base_id: The base ID
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases/{base_id}/tables"
        response = await make_airtable_request("GET", url, credentials.token)

        tables = []
        for table_data in response.get("tables", []):
            table = AirtableTable.model_validate(table_data)
            tables.append(table)

        base = AirtableBase(
            id=base_id,
            name="",
            tables=tables,
        )
        return {"base": base}
    except Exception as e:
        logger.error(f"Error getting base schema: {str(e)}")
        return {"error": str(e)}


@tool
async def create_airtable_base(
    name: str,
    workspace_id: str,
    tables: Optional[List[Dict[str, Any]]] = None,
    config: RunnableConfig = None,
) -> CreateBaseResult:
    """
    Create a new Airtable base in a specified workspace.

    Args:
        name: Base name
        workspace_id: Workspace ID where the base will be created
        tables: List of table definitions
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/bases"
        data = {"name": name, "workspaceId": workspace_id}

        if tables:
            data["tables"] = tables

        response = await make_airtable_request(
            "POST", url, credentials.token, data=data
        )
        base = AirtableBase.model_validate(response)
        return {"base": base}
    except Exception as e:
        logger.error(f"Error creating base: {str(e)}")
        return {"error": str(e)}


@tool
async def get_airtable_user_info(
    config: RunnableConfig = None,
) -> GetUserInfoResult:
    """
    Get current user information and API scopes.

    Args:
        config: Configuration containing user_id.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_airtable_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Airtable credentials")

        url = f"{META_BASE_URL}/whoami"
        response = await make_airtable_request("GET", url, credentials.token)
        return {"user": response}
    except Exception as e:
        logger.error(f"Error getting user info: {str(e)}")
        return {"error": str(e)}


def get_airtable_tools() -> List[Tool]:
    """Get all Airtable tools."""
    return [
        list_airtable_records,
        get_airtable_record,
        update_airtable_record,
        update_multiple_airtable_records,
        create_airtable_record,
        upload_airtable_attachment,
        update_airtable_field,
        create_airtable_field,
        update_airtable_table,
        create_airtable_table,
        list_airtable_bases,
        get_airtable_base_schema,
        create_airtable_base,
        get_airtable_user_info,
    ]
