CREATE TABLE pipeline_run (
      id            BIGSERIAL    PRIMARY KEY,
      status        VARCHAR(20)  NOT NULL DEFAULT 'CREATED'
                    CHECK (status IN ('CREATED', 'RUNNING', 'FAILED', 'COMPLETED')),
      trigger       VARCHAR(20)  NOT NULL CHECK (trigger IN ('MANUAL', 'SCHEDULER')),
      window_months INTEGER NOT NULL CHECK (window_months > 0),
      created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
      updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
      started_at    TIMESTAMPTZ,
      completed_at  TIMESTAMPTZ
  );

  CREATE TABLE pipeline_step (
      id             BIGSERIAL    PRIMARY KEY,
      run_id         BIGINT       NOT NULL REFERENCES pipeline_run (id) ON DELETE CASCADE,
      step_type      VARCHAR(20)  NOT NULL
                     CHECK (step_type IN ('INGEST', 'ANALYZE_RAW', 'PROCESS', 'ANALYZE_FEATURES', 'TRAIN')),
      status         VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'RUNNING', 'FAILED', 'COMPLETED', 'SKIPPED')),
      started_at     TIMESTAMPTZ,
      completed_at   TIMESTAMPTZ,
      error_message  TEXT,
      step_data      JSONB,
      UNIQUE (run_id, step_type)
  );

  CREATE INDEX idx_pipeline_step_run_id ON pipeline_step (run_id);