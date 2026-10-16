SELECT
  a.id,
  a.name,
  a.description,
  a."workspaceId",
  EXISTS (
    SELECT 1
    FROM "AgentSkills" _ask
    JOIN "Skills" _s ON _ask."skillId" = _s.id
    WHERE _ask."agentId" = a.id
      AND _s."toolName" = 'human_handoff_trigger'
  ) AS "humanHandoffEnabled",
  a.domains,
  -- Creator (User) relation
  json_build_object(
    'id', u.id,
    'name', u.name,
    'email', u.email
  ) as creator,
  -- Folders array (just IDs)
  COALESCE(
    json_agg(json_build_object('id', f.id)) FILTER (WHERE f.id IS NOT NULL),
    '[]'::json
  ) as folders,
  -- Files array (just IDs)
  COALESCE(
    json_agg(json_build_object('id', fl.id)) FILTER (WHERE fl.id IS NOT NULL),
    '[]'::json
  ) as files,
  -- Websites array (just IDs)
  COALESCE(
    json_agg(json_build_object('id', w.id)) FILTER (WHERE w.id IS NOT NULL),
    '[]'::json
  ) as websites,
  -- QNAs array (just IDs)
  COALESCE(
    json_agg(json_build_object('id', q.id)) FILTER (WHERE q.id IS NOT NULL),
    '[]'::json
  ) as qnas,
  -- Personas array (with full details)
  COALESCE(
    json_agg(json_build_object(
      'id', p.id,
      'name', p.name,
      'avatar', p.avatar,
      'description', p.description,
      'responseStyle', p."responseStyle"
    )) FILTER (WHERE p.id IS NOT NULL),
    '[]'::json
  ) as personas
  ,
  -- Apps array (id and slug)
  COALESCE(
    jsonb_agg(DISTINCT jsonb_build_object('id', app.id, 'slug', app.slug)) FILTER (WHERE app.id IS NOT NULL),
    '[]'::jsonb
  )::json as apps
  ,
  -- Skills: library rows (JOIN Skills) + workspace CUSTOM rows (skillId null, tool use_skill).
  (
    SELECT COALESCE(jsonb_agg(sub.skill_obj), '[]'::jsonb)::json
    FROM (
      SELECT jsonb_build_object(
        'id', s.id,
        'name', s.name,
        'category', s.category,
        'toolName', s."toolName",
        'config', to_jsonb(ask.config)
      ) AS skill_obj
      FROM "AgentSkills" ask
      INNER JOIN "Skills" s ON ask."skillId" = s.id
      WHERE ask."agentId" = a.id
      UNION ALL
      SELECT jsonb_build_object(
        'id', ask.id,
        'name', COALESCE(
          ask.config->'customSkill'->>'displayName',
          wcs.config->'customSkill'->>'displayName',
          'Custom skill'
        ),
        'category', 'CUSTOM',
        'toolName', 'use_skill',
        'config', COALESCE(to_jsonb(wcs.config), '{}'::jsonb) || COALESCE(to_jsonb(ask.config), '{}'::jsonb)
      ) AS skill_obj
      FROM "AgentSkills" ask
      LEFT JOIN "WorkspaceCustomSkill" wcs ON ask."workspaceCustomSkillId" = wcs.id
      WHERE ask."agentId" = a.id
        AND ask."type" = 'CUSTOM'::"AgentSkillType"
        AND ask."skillId" IS NULL
    ) sub
  ) as skills
  ,
  (
    SELECT COALESCE(
      jsonb_agg(
        jsonb_build_object(
          'entity', cfd.entity,
          'key', cfd.key,
          'label', cfd.label,
          'type', cfd.type,
          'required', cfd.required,
          'options', cfd.options
        )
        ORDER BY cfd.entity, cfd."order"
      ) FILTER (WHERE cfd.id IS NOT NULL),
      '[]'::jsonb
    )::json
    FROM "CrmFieldDefinition" cfd
    WHERE cfd."workspaceId" = a."workspaceId"
      AND cfd."aiPopulated" = true
  ) as "crmAiPopulatedFields"
FROM "Agent" a
LEFT JOIN "User" u ON a."createdBy" = u.id
LEFT JOIN "_AgentToFolder" af ON a.id = af."A"
LEFT JOIN "Folder" f ON af."B" = f.id AND f."workspaceId" = a."workspaceId"
LEFT JOIN "_AgentToFile" afl ON a.id = afl."A"
LEFT JOIN "File" fl ON afl."B" = fl.id AND fl."workspaceId" = a."workspaceId"
LEFT JOIN "_AgentToWebsites" aw ON a.id = aw."A"
LEFT JOIN "Websites" w ON aw."B" = w.id AND w."workspaceId" = a."workspaceId"
LEFT JOIN "_AgentToQna" aq ON a.id = aq."A"
LEFT JOIN "Qna" q ON aq."B" = q.id AND q."workspaceId" = a."workspaceId"
LEFT JOIN "_AgentToPersonas" ap ON a.id = ap."A"
LEFT JOIN "Personas" p ON ap."B" = p.id AND p."workspaceId" = a."workspaceId"
LEFT JOIN "_AgentToApps" aa ON a.id = aa."A"
LEFT JOIN "Apps" app ON aa."B" = app.id AND app."workspaceId" = a."workspaceId"
WHERE a.id = $1  -- Replace $1 with actual agentId
GROUP BY a.id, a.name, a.description, a."workspaceId", u.id, u.name, u.email;
