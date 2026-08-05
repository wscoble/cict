# cict — Cost-of-goods Inventory Cost Tracker

A minimal web app for small-batch producers to track **inventory quantities**,
**per-unit ingredient/material costs**, and **basic sales** — replacing
QuickBooks Online inventory with something that actually works for a small
production business.

Built from a qualified lead (`custom-inventory-cost-tracker-for-small-biz`):
a small-batch bottle producer frustrated with QBO's "horribly shitty" inventory
tracking. Each bottle has ~$1.28 in tracked ingredients — they need cost-of-goods
tied to inventory.

## Stack

- **Go** (net/http, stdlib) — single binary, no framework
- **htmx** — progressive enhancement (pages work without JS, htmx adds partial
  swaps for snappy interactions)
- **SQLite** — embedded, zero-config storage
- **go:embed** — templates compiled into the binary

## Features

- **Inventory items** — name, SKU, quantity on hand, selling price, unit
- **Ingredients per item** — track each material's cost-per-unit and
  quantity-per-bottle; the app computes cost-per-bottle automatically
- **Sales** — record sales; inventory auto-decrements
- **Cost-of-goods summary** — total inventory cost, total value (at selling
  price), total sales revenue, per-item breakdown

## Run

```bash
go build -o cict .
./cict   # serves http://localhost:8080
```

SQLite database (`cict.db`) is created in the working directory on first run.

## Progressive enhancement

Every page renders fully server-side. htmx (`<script src="...htmx.org...">`)
intercepts form submissions and link clicks to swap partial responses — but if
JS is disabled, the same forms POST and redirect normally. No build step, no
client-side bundle.

## Origin

Built by the [super-simple-software-factory](https://github.com/sscoble/super-simple-software-factory)
from a lead in the lead-gen pipeline. The factory's plan→build→test→commit
workflow generated the initial implementation from the lead's MVP scope.
