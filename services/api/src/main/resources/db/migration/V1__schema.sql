-- Esquema inicial do Alerta de Cotação. O Flyway aplica na subida da API;
-- mudanças futuras entram em V2__..., V3__... (nunca editar um arquivo já aplicado).

create table alert (
    id          uuid           primary key,
    asset       varchar(20)    not null,
    name        varchar(60),
    direction   varchar(5)     not null check (direction in ('BELOW', 'ABOVE')),
    threshold   numeric(18, 6) not null check (threshold > 0),
    rearm_pct   numeric(6, 3)  not null default 0.5 check (rearm_pct between 0 and 50),
    enabled     boolean        not null default true,
    state       varchar(10)    not null default 'ARMED' check (state in ('ARMED', 'FIRED')),
    fired_at    timestamptz,
    created_at  timestamptz    not null default now(),
    updated_at  timestamptz    not null default now(),
    version     bigint         not null default 0
);

-- Uma linha por cotação recebida do checker. A mesma cotação (ativo + horário da
-- fonte) chega repetida enquanto a fonte não atualiza: a chave primária descarta.
create table quote (
    asset        varchar(20)    not null,
    quoted_at    timestamptz    not null,
    price        numeric(18, 6) not null check (price > 0),
    source       varchar(20)    not null,
    received_at  timestamptz    not null default now(),
    primary key (asset, quoted_at)
);

-- Histórico e outbox de push: o checker grava o evento (a API aplica a mudança de
-- estado no alerta na mesma transação) e depois marca delivered_at quando o push sai.
create table alert_event (
    id            uuid           primary key,
    alert_id      uuid           references alert (id) on delete set null,
    asset         varchar(20)    not null,
    type          varchar(15)    not null check (type in ('FIRED', 'REARMED', 'SOURCE_DOWN', 'SOURCE_UP')),
    direction     varchar(5)     check (direction in ('BELOW', 'ABOVE')),
    price         numeric(18, 6),
    quoted_at     timestamptz,
    title         varchar(200)   not null,
    message       varchar(500)   not null,
    push          boolean        not null,
    occurred_at   timestamptz    not null,
    delivered_at  timestamptz,
    created_at    timestamptz    not null default now()
);

create index alert_event_occurred_at_idx on alert_event (occurred_at desc);
create index alert_event_pending_idx on alert_event (occurred_at) where push and delivered_at is null;
create index alert_event_alert_id_idx on alert_event (alert_id);
