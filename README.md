# ClearPath

ClearPath is a working Customer Success MVP that compares customer requests with a product roadmap. Roadmap items can be maintained manually or synchronized from Notion.

It also supports deal-value tracking, CSV feedback imports, and an opportunity radar that groups semantically similar customer requests.

The dashboard can generate a persisted Claude strategic insight covering demand patterns, roadmap gaps, revenue risks, and a recommended next action. Request detail pages include copy, product-share tracking, customer-update tracking, and resolve/reopen actions.

## Run with Docker

1. Copy `.env.example` to `.env`.
2. Choose `MATCHING_PROVIDER=demo` for safe local deterministic matching, or `MATCHING_PROVIDER=claude` and set `ANTHROPIC_API_KEY`.
3. Optionally set `NOTION_TOKEN` and `NOTION_DATA_SOURCE_ID`.
4. Run `docker compose up --build`.
5. Open <http://localhost:8000>.

Data is stored in a persistent Docker volume.

## Safe public demo mode

Use demo mode when deploying to Render without an Anthropic API key:

```env
MATCHING_PROVIDER=demo
SEED_DEMO_DATA=true
ANTHROPIC_API_KEY=
DATABASE_URL=sqlite:////tmp/clearpath.db
```

Demo mode performs deterministic local roadmap matching, request-theme grouping, customer-note generation, and dashboard insights. It makes no Anthropic network calls and displays a visible **Safe demo mode** banner. Notion credentials are also optional; add roadmap items manually if you do not want to store a Notion token on Render.

`SEED_DEMO_DATA=true` makes an empty deployment immediately presentation-ready with sample roadmap items, varied checked requests, deal blockers, activity states, opportunity groups, and a strategic insight. Seeding is idempotent and never duplicates or overwrites existing content.

## Notion roadmap schema

Share the data source with your Notion integration and create these exact properties:

| Property | Notion type |
| --- | --- |
| Name | Title |
| Description | Rich text |
| Status | Status |
| Quarter | Select |
| Priority | Select |

Use the data source ID, not a page URL, for `NOTION_DATA_SOURCE_ID`. Syncs upsert Notion rows and mark removed Notion rows inactive. Manual roadmap items are never changed by a Notion sync.

## Local development and tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest
```

The app requires at least one active roadmap item before it can run a Claude check.

## Feedback CSV format

Upload up to 25 requests at a time from the **Import feedback** page. Only `request` is required.

After upload, ClearPath opens a live progress page showing the current company, processed count, completion percentage, successful checks, and failures.

```csv
company,contact,request,deal_value,deal_stage,deal_blocker
Acme,Sarah,Need enterprise SSO,48000,Negotiation,true
GrowthLabs,Ana,Custom dashboards per team,25000,Evaluation,false
```

`deal_value` is treated as euros. Similar requests are grouped using a normalized request theme.
