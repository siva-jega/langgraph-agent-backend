import json
import logging
from typing import Any, Dict, List, Optional

import asyncpg

from app.core.config import app_settings
from app.core.types import MessageRole
from common.db_queries import (
    get_agent_query,
    get_create_messages_query,
)
from common.infrastructure import get_pg, get_redis
from app.modules.common.conversation_live_client import (
    schedule_conversation_live_activity,
)

logger = logging.getLogger(__name__)

class AgentsRepository:
    """Repository for agents-related data access."""

    def __init__(self) -> None:
        pass

    async def chat_exists(self, chat_id: str) -> bool:
        """Return True if an AgentChat exists for the given id."""
        try:
            pg = get_pg()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(
                    'SELECT 1 FROM "AgentChats" WHERE id = $1 LIMIT 1', chat_id
                )
            return row is not None
        except RuntimeError:
            # DATABASE_URL not set or lifespan not run
            return False
        except Exception:
            logger.exception("Error checking chat existence for %s", chat_id)
            return False

    async def chat_needs_location(self, chat_id: str) -> bool:
        """Return True if chat is missing location fields (or chat doesn't exist).

        This is used to decide whether to attempt GeoIP enrichment. It avoids repeatedly
        doing lookups once a chat already has latitude/longitude/country/city.
        """
        try:
            pg = get_pg()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT latitude, longitude, country, city
                    FROM "AgentChats"
                    WHERE id = $1
                    LIMIT 1
                    """,
                    chat_id,
                )
            if row is None:
                return True
            return (
                row.get("latitude") is None
                or row.get("longitude") is None
                or row.get("country") is None
                or row.get("city") is None
            )
        except RuntimeError:
            # DATABASE_URL not set or lifespan not run
            return False
        except Exception:
            logger.exception("Error checking chat location for %s", chat_id)
            return False

    async def save_conversation(
        self,
        chat_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        user_message: str,
        assistant_content: str,
        sources: List[Any],
        token_data: int,
        extra_info: Optional[dict],
        response_time: float,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        country: Optional[str] = None,
        city: Optional[str] = None,
        *,
        channel: str = "WEB",
        chat_title: Optional[str] = None,
        integration_id: Optional[str] = None,
    ) -> bool:
        """Save messages and update usage. Returns True on success, False otherwise.

        Creates user and assistant message dicts internally, then saves them to DB
        using SQL query from db/queries/messages.sql, and updates usage credits.
        """
        try:
            user_stripped = (user_message or "").strip()
            assistant_stripped = (assistant_content or "").strip()

            if not user_stripped and not assistant_stripped:
                logger.warning(
                    "save_conversation: empty user and assistant for chat %s, skip",
                    chat_id,
                )
                return False

            messages: List[dict] = []
            if user_stripped:
                messages.append(
                    {
                        "role": MessageRole.USER.value,
                        "message": user_message,
                    }
                )
            if assistant_stripped:
                messages.append(
                    {
                        "role": MessageRole.ASSISTANT.value,
                        "message": assistant_content,
                        "token_consumed": token_data,
                        "sources": sources if sources else None,
                        "extra_info": extra_info if extra_info else None,
                        "response_time": response_time,
                    }
                )

            logger.info(
                "Saving message for user %s with tokens: %s", user_id, token_data
            )

            # Save messages to DB using SQL query
            try:
                pg = get_pg()
                query = get_create_messages_query()

                # Prepare messages JSONB array
                messages_json = []
                for msg in messages:
                    msg_dict: dict[str, Any] = {
                        "message": msg.get("message", ""),
                        "role": msg.get("role", ""),
                    }
                    # Handle sources (can be dict/list or None)
                    raw_sources = msg.get("sources")
                    if raw_sources:
                        msg_dict["sources"] = raw_sources
                    else:
                        msg_dict["sources"] = None

                    # Handle extra_info (can be dict or None)
                    raw_extra = msg.get("extra_info")
                    if raw_extra:
                        msg_dict["extra_info"] = raw_extra
                    else:
                        msg_dict["extra_info"] = None

                    msg_dict["token_consumed"] = msg.get("token_consumed", 0)
                    msg_dict["response_time"] = msg.get("response_time", 0)
                    messages_json.append(msg_dict)

                messages_jsonb = json.dumps(messages_json)

                if not user_id:
                    logger.warning(
                        "user_id is required but not provided for chat %s", chat_id
                    )
                    return False

                c = str(channel).strip().lower()
                if c == "whatsapp":
                    db_channel = "WHATSAPP"
                elif c == "instagram":
                    db_channel = "INSTAGRAM"
                elif c == "gmail":
                    db_channel = "GMAIL"
                else:
                    db_channel = "WEB"
                title_arg = (chat_title or "").strip()

                logger.info(
                    "Saving conversation: chat_id=%s, user_id=%s, agent_id=%s, response_time=%s, channel=%s",
                    chat_id,
                    user_id,
                    agent_id,
                    response_time,
                    db_channel,
                )

                async with pg.acquire() as conn:
                    await conn.execute(
                        query,
                        chat_id,
                        "AGENT",
                        session_id or "",
                        title_arg,
                        user_id,
                        agent_id or "",
                        messages_jsonb,
                        latitude,
                        longitude,
                        country,
                        city,
                        db_channel,
                        (integration_id or "").strip(),
                    )

                    if session_id and agent_id:
                        await self._resolve_and_link_session(
                            conn, chat_id, agent_id, session_id, db_channel
                        )

                logger.info(
                    "Successfully created chat %s with %s messages",
                    chat_id,
                    len(messages),
                )
                schedule_conversation_live_activity(chat_id)
                logger.info(
                    "conversation_live: scheduled activity bump after save chat_id=%s",
                    chat_id,
                )
                return True
            except RuntimeError:
                logger.warning("DATABASE_URL not set; skipping save_conversation")
                return False
            except Exception as e:
                logger.error("Error saving messages to DB: %s", e, exc_info=True)
                return False
        except Exception as e:
            logger.error("Error saving conversation and usage: %s", e, exc_info=True)
            return False

    async def _resolve_and_link_session(
        self,
        conn,
        chat_id: str,
        agent_id: str,
        session_id: str,
        channel: str,
    ) -> None:
        """Find-or-create AgentChatSessions row and link the chat to it."""
        try:
            workspace_row = await conn.fetchrow(
                'SELECT "workspaceId" FROM "Agent" WHERE id = $1 LIMIT 1',
                agent_id,
            )
            workspace_id = workspace_row["workspaceId"] if workspace_row else ""

            row = await conn.fetchrow(
                'SELECT id FROM "AgentChatSessions" '
                'WHERE "agentId" = $1 AND "clientSessionKey" = $2 LIMIT 1',
                agent_id,
                session_id,
            )
            if row:
                session_pk = row["id"]
            else:
                import uuid as _uuid

                session_pk = str(_uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO "AgentChatSessions"
                      (id, "workspaceId", "agentId", channel, "clientSessionKey", "createdAt", "updatedAt")
                    VALUES ($1, $2, $3, $4::"AgentChatChannel", $5, NOW(), NOW())
                    ON CONFLICT ("agentId", "clientSessionKey") DO NOTHING
                    """,
                    session_pk,
                    workspace_id,
                    agent_id,
                    channel,
                    session_id,
                )
                refetch = await conn.fetchrow(
                    'SELECT id FROM "AgentChatSessions" '
                    'WHERE "agentId" = $1 AND "clientSessionKey" = $2 LIMIT 1',
                    agent_id,
                    session_id,
                )
                if refetch:
                    session_pk = refetch["id"]

            await conn.execute(
                'UPDATE "AgentChats" SET "sessionId" = $1 WHERE id = $2',
                session_pk,
                chat_id,
            )
        except Exception:
            logger.warning(
                "Failed to resolve/link session for chat %s (non-fatal)", chat_id,
                exc_info=True,
            )

    def _get_cache_key(self, agent_id: str) -> str:
        """Get Redis cache key for agent config."""
        return f"agent:config:{agent_id}"

    async def get_config(self, agent_id: str) -> Optional[dict]:
        """Get the config for an agent. Checks Redis first, then DB, then caches in Redis."""
        # cache_key = self._get_cache_key(agent_id)

        try:
            # Try Redis first
            # try:
            #     redis = get_redis()
            #     cached = await redis.get_json(cache_key)
            #     if cached is not None:
            #         logger.debug("Agent config %s found in Redis cache", agent_id)
            #         return cached
            # except RuntimeError:
            #     logger.debug("Redis not available, skipping cache check")
            # except Exception as e:
            #     logger.debug("Redis cache check failed: %s", e)

            # Cache miss - fetch from DB
            try:
                pg = get_pg()
                query = get_agent_query()

                async with pg.acquire() as conn:
                    row = await conn.fetchrow(query, agent_id)

                if not row:
                    logger.warning("Agent %s not found", agent_id)
                    return None

                # Extract data from row with proper type handling
                human_handoff_enabled = row.get("humanHandoffEnabled", False) or False
                # Handle creator (should be JSON object or dict)
                creator_raw = row.get("creator")
                if isinstance(creator_raw, dict):
                    creator = creator_raw
                elif isinstance(creator_raw, str) and creator_raw.strip():
                    try:
                        creator = json.loads(creator_raw)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Invalid creator JSON for agent %s: %s",
                            agent_id,
                            creator_raw,
                        )
                        creator = {}
                else:
                    creator = {}

                # Handle personas (should be JSON array or list)
                personas_raw = row.get("personas")
                if isinstance(personas_raw, list):
                    personas = personas_raw
                elif isinstance(personas_raw, str) and personas_raw.strip():
                    try:
                        parsed = json.loads(personas_raw)
                        personas = parsed if isinstance(parsed, list) else []
                    except json.JSONDecodeError:
                        logger.warning(
                            "Invalid personas JSON for agent %s: %s",
                            agent_id,
                            personas_raw,
                        )
                        personas = []
                else:
                    personas = []

                # Get first persona if available, otherwise use defaults
                persona_data = {}
                if personas and len(personas) > 0:
                    first_persona = personas[0]
                    if isinstance(first_persona, dict):
                        persona_data = first_persona
                    elif isinstance(first_persona, str):
                        try:
                            persona_data = (
                                json.loads(first_persona) if first_persona else {}
                            )
                        except json.JSONDecodeError:
                            logger.warning("Invalid persona JSON: %s", first_persona)
                            persona_data = {}

                persona_name = (
                    persona_data.get("name", "")
                    if isinstance(persona_data, dict)
                    else ""
                )
                persona_description = (
                    persona_data.get("description", "")
                    if isinstance(persona_data, dict)
                    else ""
                )
                default_model = app_settings.DEFAULT_AGENT_MODEL
                model = (
                    (persona_data.get("model") or default_model)
                    if isinstance(persona_data, dict)
                    else default_model
                )

                # Extract entityIds from folders, files, websites, qnas
                def safe_get_list(key: str) -> list:
                    """Safely extract list from row, handling JSON strings."""
                    raw = row.get(key)
                    if isinstance(raw, list):
                        return raw
                    if isinstance(raw, str) and raw.strip():
                        try:
                            parsed = json.loads(raw)
                            return parsed if isinstance(parsed, list) else []
                        except json.JSONDecodeError:
                            logger.warning(
                                "Invalid %s JSON for agent %s: %s", key, agent_id, raw
                            )
                            return []
                    return []

                folders = safe_get_list("folders")
                files = safe_get_list("files")
                websites = safe_get_list("websites")
                qnas = safe_get_list("qnas")
                apps = safe_get_list("apps")

                entity_ids = []
                folder_ids = []
                apps_config = []
                for folder in folders:
                    if isinstance(folder, dict) and folder.get("id"):
                        folder_ids.append(folder["id"])
                for file in files:
                    if isinstance(file, dict) and file.get("id"):
                        entity_ids.append(file["id"])
                for website in websites:
                    if isinstance(website, dict) and website.get("id"):
                        entity_ids.append(website["id"])
                for qna in qnas:
                    if isinstance(qna, dict) and qna.get("id"):
                        entity_ids.append(qna["id"])

                logger.info(
                    "Fetched entity_ids for agent %s: %s",
                    agent_id,
                    entity_ids,
                )

                for app in apps:
                    if isinstance(app, dict) and app.get("id"):
                        apps_config.append({
                            "id": app["id"],
                            "name": app.get("name", ""),
                            "slug": app.get("slug", ""),
                            "description": app.get("description", ""),
                            "ingestion_status": app.get("ingestionStatus", ""),
                        })

                # Extract user_id from creator
                user_id_from_config = (
                    creator.get("id", "") if isinstance(creator, dict) else ""
                )

                # Extract apps (list of objects with id and slug). Accepts JSON string or list.
                apps_raw = row.get("apps")
                apps = []
                raw_apps_list = []
                if isinstance(apps_raw, list):
                    raw_apps_list = apps_raw
                elif isinstance(apps_raw, str) and apps_raw.strip():
                    try:
                        parsed_apps = json.loads(apps_raw)
                        raw_apps_list = parsed_apps if isinstance(parsed_apps, list) else []
                    except json.JSONDecodeError:
                        logger.warning(
                            "Invalid apps JSON for agent %s: %s", agent_id, apps_raw
                        )
                        raw_apps_list = []
                else:
                    raw_apps_list = []

                for app in raw_apps_list:
                    if isinstance(app, dict):
                        app_id = app.get("id") or app.get("appId") or app.get("app_id") or ""
                        slug = app.get("slug") or app.get("appSlug") or app.get("app_slug") or ""
                        apps.append({"id": app_id, "slug": slug})

                # Extract skills (list of { id, name, category, config })
                skills_raw = row.get("skills")
                skills = []
                if isinstance(skills_raw, list):
                    for sk in skills_raw:
                        if isinstance(sk, dict) and (sk.get("id") or sk.get("name")):
                            skills.append({
                                "id": sk.get("id", ""),
                                "name": sk.get("name", ""),
                                "category": sk.get("category"),
                                "toolName": (sk.get("toolName") or "").strip() or None,
                                "config": sk.get("config") if isinstance(sk.get("config"), dict) else {},
                            })
                elif isinstance(skills_raw, str) and skills_raw.strip():
                    try:
                        parsed = json.loads(skills_raw)
                        if isinstance(parsed, list):
                            for sk in parsed:
                                if isinstance(sk, dict) and (sk.get("id") or sk.get("name")):
                                    skills.append({
                                        "id": sk.get("id", ""),
                                        "name": sk.get("name", ""),
                                        "category": sk.get("category"),
                                        "toolName": (sk.get("toolName") or "").strip() or None,
                                        "config": sk.get("config") if isinstance(sk.get("config"), dict) else {},
                                    })
                    except json.JSONDecodeError:
                        logger.warning("Invalid skills JSON for agent %s: %s", agent_id, skills_raw[:200])

                # Extract apps (list of objects with id and slug). Accepts JSON string or list.
                apps_raw = row.get("apps")
                apps = []
                raw_apps_list = []
                if isinstance(apps_raw, list):
                    raw_apps_list = apps_raw
                elif isinstance(apps_raw, str) and apps_raw.strip():
                    try:
                        parsed_apps = json.loads(apps_raw)
                        raw_apps_list = parsed_apps if isinstance(parsed_apps, list) else []
                    except json.JSONDecodeError:
                        logger.warning(
                            "Invalid apps JSON for agent %s: %s", agent_id, apps_raw
                        )
                        raw_apps_list = []
                else:
                    raw_apps_list = []

                for app in raw_apps_list:
                    if isinstance(app, dict):
                        app_id = app.get("id") or app.get("appId") or app.get("app_id") or ""
                        slug = app.get("slug") or app.get("appSlug") or app.get("app_slug") or ""
                        apps.append({"id": app_id, "slug": slug})

                # Extract domains (postgres text[] comes back as list[str] via asyncpg)
                domains_raw = row.get("domains")
                if isinstance(domains_raw, list):
                    domains = [d for d in domains_raw if isinstance(d, str) and d]
                else:
                    domains = []

                crm_ai_fields = row.get("crmAiPopulatedFields")
                if not isinstance(crm_ai_fields, list):
                    crm_ai_fields = []

                config_data = {
                    "agent_id": agent_id,
                    "user_id": user_id_from_config,
                    "domains": domains,
                    "config": {
                        "human_handoff_enabled": human_handoff_enabled,
                        "apps": apps if apps else [],  # list of {"id","slug"}
                        "skills": skills,
                        "crmAiPopulatedFields": crm_ai_fields,
                        "entityIds": entity_ids,
                        "folderIds": folder_ids,
                        "appsConfig": apps_config,
                        "persona": {
                            "personaName": persona_name,
                            "personaDescription": persona_description,
                            "model": model,
                        },
                        "ownerEmail": (
                            creator.get("email", "")
                            if isinstance(creator, dict)
                            else ""
                        )
                    },
                }

                # Cache in Redis (1 hour TTL)
                # try:
                #     redis = get_redis()
                #     await redis.set_json(cache_key, config_data, ex=3600)
                #     logger.debug("Cached agent config %s in Redis", agent_id)
                # except RuntimeError:
                #     pass  # Redis not available, skip caching
                # except Exception as e:
                #     logger.debug("Failed to cache config in Redis: %s", e)

                return config_data
            except RuntimeError:
                logger.warning(
                    "DATABASE_URL not set; returning default config (no DB). "
                    "WhatsApp Flow tools will not auto-inject without whatsappFlowContext from agent.sql — set DATABASE_URL to the same Postgres user-backend uses."
                )
                return {
                    "agent_id": agent_id,
                    "config": {
                        "human_handoff_enabled": False,
                        "apps": [],
                        "skills": [],
                        "entityIds": [],
                        "folderIds": [],
                        "appsConfig": [],
                        "persona": {
                            "personaName": "",
                            "personaDescription": "",
                            "model": app_settings.DEFAULT_AGENT_MODEL,
                        },
                        "ownerEmail": "",
                        "whatsappFlowContext": None,
                    },
                }
        except Exception as e:
            logger.error("Error getting config: %s", e, exc_info=True)
            raise

    async def invalidate_config_cache(self, agent_id: str) -> bool:
        """Invalidate Redis cache for agent config. Returns True if deleted, False otherwise."""
        cache_key = self._get_cache_key(agent_id)
        try:
            redis = get_redis()
            deleted = await redis.delete(cache_key)
            if deleted:
                logger.info("Invalidated cache for agent config %s", agent_id)
            return bool(deleted)
        except RuntimeError:
            logger.debug("Redis not available, cannot invalidate cache")
            return False
        except Exception as e:
            logger.error("Error invalidating cache: %s", e, exc_info=True)
            return False
