-- Workflow Service Schema
-- Idempotent DDL: safe to run on every startup

CREATE SCHEMA IF NOT EXISTS workflow;

-- Enums (wrapped in DO blocks for idempotency)
DO $$ BEGIN
    CREATE TYPE workflow."WorkflowStatus" AS ENUM ('DRAFT', 'ACTIVE', 'INACTIVE');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workflow."TriggerType" AS ENUM ('CONVERSATION_EVENT', 'SCHEDULE', 'WEBHOOK', 'MANUAL', 'INTEGRATION_EVENT');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workflow."StepType" AS ENUM ('CONDITION', 'WAIT', 'SEND_MESSAGE', 'HTTP_REQUEST', 'RUN_AI_TASK', 'UPDATE_RECORD', 'CONNECTOR');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workflow."ErrorPolicy" AS ENUM ('FAIL', 'SKIP', 'RETRY');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workflow."RunStatus" AS ENUM ('QUEUED', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workflow."StepRunStatus" AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 1. workflow_definition
CREATE TABLE IF NOT EXISTS workflow.workflow_definition (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    "workspaceId"     UUID            NOT NULL,
    name              VARCHAR(255)    NOT NULL,
    description       TEXT,
    status            workflow."WorkflowStatus" NOT NULL DEFAULT 'DRAFT',
    version           INTEGER         NOT NULL DEFAULT 1,
    "definitionJson"  JSONB           NOT NULL DEFAULT '{}',
    "createdAt"       TIMESTAMPTZ     NOT NULL DEFAULT now(),
    "updatedAt"       TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT chk_def_json_size CHECK (octet_length("definitionJson"::text) <= 262144)
);
CREATE INDEX IF NOT EXISTS idx_wf_def_workspace_status ON workflow.workflow_definition ("workspaceId", status);

-- 2. workflow_trigger
CREATE TABLE IF NOT EXISTS workflow.workflow_trigger (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    "workflowDefinitionId"  UUID            NOT NULL REFERENCES workflow.workflow_definition(id) ON DELETE CASCADE,
    type                    workflow."TriggerType" NOT NULL,
    "configJson"            JSONB           NOT NULL DEFAULT '{}',
    "webhookUrl"            VARCHAR(512)    UNIQUE,
    "agentId"               UUID,
    "filterJson"            JSONB,
    "isActive"              BOOLEAN         NOT NULL DEFAULT true,
    "createdAt"             TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wf_trigger_def ON workflow.workflow_trigger ("workflowDefinitionId");
CREATE INDEX IF NOT EXISTS idx_wf_trigger_type_active ON workflow.workflow_trigger (type, "isActive") WHERE "isActive" = true;

-- 3. workflow_step
CREATE TABLE IF NOT EXISTS workflow.workflow_step (
    id                      UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    "workflowDefinitionId"  UUID                NOT NULL REFERENCES workflow.workflow_definition(id) ON DELETE CASCADE,
    "stepId"                VARCHAR(100)        NOT NULL,
    type                    workflow."StepType"  NOT NULL,
    label                   VARCHAR(255)        NOT NULL,
    "configJson"            JSONB               NOT NULL DEFAULT '{}',
    "positionX"             DOUBLE PRECISION    NOT NULL DEFAULT 0,
    "positionY"             DOUBLE PRECISION    NOT NULL DEFAULT 0,
    "errorPolicy"           workflow."ErrorPolicy" NOT NULL DEFAULT 'FAIL',
    "createdAt"             TIMESTAMPTZ         NOT NULL DEFAULT now(),

    CONSTRAINT uq_wf_step_def_stepid UNIQUE ("workflowDefinitionId", "stepId")
);

-- 4. workflow_connection
CREATE TABLE IF NOT EXISTS workflow.workflow_connection (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    "workflowDefinitionId"  UUID            NOT NULL REFERENCES workflow.workflow_definition(id) ON DELETE CASCADE,
    "sourceStepId"          VARCHAR(100)    NOT NULL,
    "targetStepId"          VARCHAR(100)    NOT NULL,
    "sourceOutput"          VARCHAR(100),
    "targetInput"           VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_wf_conn_def ON workflow.workflow_connection ("workflowDefinitionId");

-- 5. workflow_run
CREATE TABLE IF NOT EXISTS workflow.workflow_run (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    "workflowDefinitionId"  UUID            NOT NULL REFERENCES workflow.workflow_definition(id),
    "triggerId"             UUID            REFERENCES workflow.workflow_trigger(id) ON DELETE SET NULL,
    status                  workflow."RunStatus" NOT NULL DEFAULT 'QUEUED',
    "correlationId"         VARCHAR(255),
    "inputData"             JSONB,
    "errorMessage"          TEXT,
    "startedAt"             TIMESTAMPTZ,
    "completedAt"           TIMESTAMPTZ,
    "createdAt"             TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wf_run_def_status ON workflow.workflow_run ("workflowDefinitionId", status);
CREATE INDEX IF NOT EXISTS idx_wf_run_correlation ON workflow.workflow_run ("correlationId") WHERE "correlationId" IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wf_run_created ON workflow.workflow_run ("createdAt" DESC);

-- 6. workflow_run_step
CREATE TABLE IF NOT EXISTS workflow.workflow_run_step (
    id                UUID                    PRIMARY KEY DEFAULT gen_random_uuid(),
    "workflowRunId"   UUID                    NOT NULL REFERENCES workflow.workflow_run(id) ON DELETE CASCADE,
    "stepId"          VARCHAR(100)            NOT NULL,
    status            workflow."StepRunStatus" NOT NULL DEFAULT 'PENDING',
    "inputData"       JSONB,
    "outputData"      JSONB,
    "errorMessage"    TEXT,
    "retryCount"      INTEGER                 NOT NULL DEFAULT 0,
    "startedAt"       TIMESTAMPTZ,
    "completedAt"     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_wf_runstep_run ON workflow.workflow_run_step ("workflowRunId");
CREATE INDEX IF NOT EXISTS idx_wf_runstep_run_status ON workflow.workflow_run_step ("workflowRunId", status);
