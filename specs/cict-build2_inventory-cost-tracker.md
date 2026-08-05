# Plan: Inventory Cost Tracker for Small-Batch Production

## Summary

Build a Go + htmx + SQLite web app that replaces QuickBooks Online inventory for a small-batch bottled-product business. Tracks inventory quantities, per-unit ingredient/material costs, and basic sales. Single binary, single-user, minimal.

## Architecture

```
cict/
├── main.go              # entry point, router, server start
├── store.go             # SQLite init, migrations, all CRUD
├── handlers.go          # HTTP handlers (page renders + API actions)
├── templates.go         # template embedding, parsing, render helpers
├── templates/
│   ├── base.html        # outer shell: doctype, nav, htmx CDN, minimal CSS
│   ├── home.html        # dashboard: quick stats + links
│   ├── inventory_list.html
│   ├── inventory_form.html   # shared add/edit form
│   ├── inventory_detail.html # single item + its ingredients table
│   ├── ingredient_form.html  # add/edit ingredient row
│   ├── sales_list.html
│   ├── sales_form.html
│   └── summary.html     # cost-of-goods summary
├── go.mod
└── go.sum
```

**Dependencies** (beyond stdlib):
- `modernc.org/sqlite` — pure-Go SQLite driver (no CGO, single-binary friendly)

## Database Schema

Three tables in a single SQLite file (`cict.db` in the working directory):

```sql
CREATE TABLE inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'bottle',
    quantity_on_hand INTEGER NOT NULL DEFAULT 0,
    selling_price REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    cost_per_unit REAL NOT NULL,
    unit TEXT NOT NULL,
    quantity_per_bottle REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id),
    quantity INTEGER NOT NULL,
    sale_price REAL NOT NULL,
    sale_date TEXT NOT NULL DEFAULT (date('now')),
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Routes

All routes use Go 1.22+ `http.ServeMux` method+path patterns.

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/` | `handleHome` | Dashboard with quick stats |
| GET | `/inventory` | `handleInventoryList` | List all inventory items |
| GET | `/inventory/new` | `handleInventoryNew` | Add-item form |
| POST | `/inventory` | `handleInventoryCreate` | Create item, redirect to list |
| GET | `/inventory/{id}` | `handleInventoryDetail` | Item detail + ingredients |
| GET | `/inventory/{id}/edit` | `handleInventoryEdit` | Edit-item form |
| PUT | `/inventory/{id}` | `handleInventoryUpdate` | Update item, redirect to detail |
| DELETE | `/inventory/{id}` | `handleInventoryDelete` | Delete item, redirect to list |
| GET | `/inventory/{id}/ingredients/new` | `handleIngredientNew` | Add-ingredient form |
| POST | `/inventory/{id}/ingredients` | `handleIngredientCreate` | Create ingredient, redirect to detail |
| GET | `/inventory/{id}/ingredients/{ingId}/edit` | `handleIngredientEdit` | Edit-ingredient form |
| PUT | `/inventory/{id}/ingredients/{ingId}` | `handleIngredientUpdate` | Update ingredient, redirect to detail |
| DELETE | `/inventory/{id}/ingredients/{ingId}` | `handleIngredientDelete` | Delete ingredient, redirect to detail |
| GET | `/sales` | `handleSalesList` | List all sales |
| GET | `/sales/new` | `handleSalesNew` | Record-sale form |
| POST | `/sales` | `handleSalesCreate` | Create sale, redirect to sales list |
| GET | `/summary` | `handleSummary` | Cost-of-goods summary |

## htmx Strategy

- **CDN**: `<script src="https://unpkg.com/htmx.org@2.0.4"></script>` in `base.html`
- **Navigation**: `hx-boost="true"` on all `<a>` tags in the nav and throughout — makes page navigation use htmx (replaces `<body>`), falling back to full-page loads without JS
- **Forms**: Use `hx-post` / `hx-put` / `hx-delete` with `hx-target` for inline updates where it improves UX; standard form `action` + `method` as fallback
- **Delete buttons**: `hx-delete` with `hx-confirm="Are you sure?"` and `hx-target` to remove the row
- **Partial vs full renders**: Handlers check `r.Header.Get("HX-Request")`. If set, render only the content block. If not, render the full page (base.html wrapping the content). This keeps the non-JS path working.

## Template Structure

`base.html` defines the outer shell with a `{{define "content"}}` block. Every page template defines that block. `templates.go` provides two render helpers:

- `renderPage(w, r, name, data)` — executes `base.html` which in turn executes the named content template
- `renderPartial(w, name, data)` — executes only the named content template (for htmx requests)

Minimal CSS is inlined in `base.html` — a simple system font stack, max-width container, table styling, form layout. No external CSS files.

## Store Layer (`store.go`)

A `Store` struct wrapping `*sql.DB`. Methods:

```go
type Store struct { db *sql.DB }

// Inventory items
func (s *Store) CreateItem(name, description, unit string, qty int, price float64) (int64, error)
func (s *Store) GetItem(id int64) (*InventoryItem, error)
func (s *Store) ListItems() ([]InventoryItem, error)
func (s *Store) UpdateItem(id int64, name, description, unit string, qty int, price float64) error
func (s *Store) DeleteItem(id int64) error

// Ingredients
func (s *Store) CreateIngredient(itemID int64, name, unit string, costPerUnit, qtyPerBottle float64) (int64, error)
func (s *Store) ListIngredients(itemID int64) ([]Ingredient, error)
func (s *Store) GetIngredient(id int64) (*Ingredient, error)
func (s *Store) UpdateIngredient(id int64, name, unit string, costPerUnit, qtyPerBottle float64) error
func (s *Store) DeleteIngredient(id int64) error

// Sales
func (s *Store) CreateSale(itemID int64, qty int, price float64, date, notes string) (int64, error)
func (s *Store) ListSales() ([]Sale, error)

// Summary queries
func (s *Store) CostPerItem(itemID int64) (float64, error)  // SUM(cost_per_unit * quantity_per_bottle)
func (s *Store) SummaryStats() (*Summary, error)
```

`InitDB(path string) (*Store, error)` opens the database and runs `CREATE TABLE IF NOT EXISTS` for all three tables.

## File-by-File Build Order

### Step 1: `store.go` — Database layer
- Define `InventoryItem`, `Ingredient`, `Sale`, `Summary` structs
- Implement `InitDB` with schema creation
- Implement all CRUD methods
- Implement `CostPerItem` and `SummaryStats` queries

### Step 2: `templates.go` — Template engine
- `//go:embed templates/*` to embed all templates
- Parse templates at init time into a `*template.Template`
- Implement `renderPage` and `renderPartial` helpers
- Helper to check `isHtmxRequest(r)`

### Step 3: `templates/` — All HTML templates
- `base.html`: doctype, head with htmx CDN + minimal CSS, nav bar, `{{template "content" .}}`
- `home.html`: quick stats cards (total items, total bottles on hand, recent sales count)
- `inventory_list.html`: table of items with edit/delete actions, "Add Item" link
- `inventory_form.html`: form for name, description, unit, quantity, selling price
- `inventory_detail.html`: item info + ingredients table with add/edit/delete, computed cost per bottle
- `ingredient_form.html`: form for name, cost/unit, unit, quantity per bottle
- `sales_list.html`: table of sales with date, item, quantity, price, revenue
- `sales_form.html`: form with item dropdown, quantity, sale price, date, notes
- `summary.html`: per-product cost breakdown table, total inventory value, sales revenue, profit

### Step 4: `handlers.go` — HTTP handlers
- One handler per route
- Each handler: parse params, call store, render template
- Forms: parse `r.ParseForm()`, validate, call store, redirect on success
- Delete: call store, redirect (or return 200 with htmx trigger for row removal)

### Step 5: `main.go` — Wire everything together
- Call `InitDB("cict.db")`
- Create `http.ServeMux`, register all routes
- Start server on `:8080`

## Verification

1. `go build -o cict .` produces a single binary
2. `./cict` starts and prints "cict listening on :8080"
3. Visit `http://localhost:8080` — see dashboard
4. Add an inventory item — appears in list
5. Add ingredients to the item — see cost-per-bottle update
6. Record a sale — inventory quantity decreases, sale appears in list
7. View summary — see cost-of-goods breakdown
8. Disable JavaScript — all pages still work (full page loads, form POSTs)
9. `go vet ./...` passes clean

## Commit Message

```
[lead:custom-inventory-cost-tracker-for-small-biz] Add spec for inventory cost tracker web app
```
