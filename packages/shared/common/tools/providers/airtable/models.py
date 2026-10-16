"""Airtable data models."""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any, TypedDict


class AirtableRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    created_time: Optional[str] = Field(None, alias="createdTime")
    fields: Dict[str, Any] = {}


class AirtableField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    name: str
    type: str
    options: Optional[Dict[str, Any]] = None
    description: Optional[str] = None


class AirtableTable(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    primary_field_id: Optional[str] = Field(None, alias="primaryFieldId")
    fields: Optional[List[AirtableField]] = None
    views: Optional[List[Dict[str, Any]]] = None


class AirtableBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    permission_level: Optional[str] = Field(None, alias="permissionLevel")
    tables: Optional[List[AirtableTable]] = None


class ListRecordsResult(TypedDict):
    records: List[AirtableRecord]
    offset: Optional[str]
    error: Optional[str]


class GetRecordResult(TypedDict, total=False):
    record: AirtableRecord
    error: str


class UpdateRecordResult(TypedDict, total=False):
    record: AirtableRecord
    error: str


class UpdateMultipleRecordsResult(TypedDict):
    records: List[AirtableRecord]
    error: Optional[str]


class CreateRecordResult(TypedDict, total=False):
    record: AirtableRecord
    error: str


class UploadAttachmentResult(TypedDict, total=False):
    attachment: Dict[str, Any]
    error: str


class UpdateFieldResult(TypedDict, total=False):
    field: AirtableField
    error: str


class CreateFieldResult(TypedDict, total=False):
    field: AirtableField
    error: str


class UpdateTableResult(TypedDict, total=False):
    table: AirtableTable
    error: str


class CreateTableResult(TypedDict, total=False):
    table: AirtableTable
    error: str


class ListBasesResult(TypedDict):
    bases: List[AirtableBase]
    offset: Optional[str]
    error: Optional[str]


class GetBaseSchemaResult(TypedDict, total=False):
    base: AirtableBase
    error: str


class CreateBaseResult(TypedDict, total=False):
    base: AirtableBase
    error: str


class GetUserInfoResult(TypedDict, total=False):
    user: Dict[str, Any]
    error: str
