package main

import (
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

// ─── Data Types ───────────────────────────────────────────────────────────────

type InventoryItem struct {
	ID             int64   `json:"id"`
	Name           string  `json:"name"`
	Description    string  `json:"description"`
	Unit           string  `json:"unit"`
	QuantityOnHand int     `json:"quantity_on_hand"`
	SellingPrice   float64 `json:"selling_price"`
	CreatedAt      string  `json:"created_at"`
	UpdatedAt      string  `json:"updated_at"`
}

type Ingredient struct {
	ID               int64   `json:"id"`
	InventoryItemID  int64   `json:"inventory_item_id"`
	Name             string  `json:"name"`
	CostPerUnit      float64 `json:"cost_per_unit"`
	Unit             string  `json:"unit"`
	QuantityPerBottle float64 `json:"quantity_per_bottle"`
	CreatedAt        string  `json:"created_at"`
	UpdatedAt        string  `json:"updated_at"`
}

type Sale struct {
	ID              int64   `json:"id"`
	InventoryItemID int64   `json:"inventory_item_id"`
	ItemName        string  `json:"item_name,omitempty"`
	Quantity        int     `json:"quantity"`
	SalePrice       float64 `json:"sale_price"`
	SaleDate        string  `json:"sale_date"`
	Notes           string  `json:"notes"`
	CreatedAt       string  `json:"created_at"`
}

type Summary struct {
	Items          []SummaryItem `json:"items"`
	TotalValue     float64       `json:"total_value"`
	TotalSales     float64       `json:"total_sales"`
	TotalCost      float64       `json:"total_cost"`
	GrossProfit    float64       `json:"gross_profit"`
	TotalBottles   int           `json:"total_bottles"`
}

type SummaryItem struct {
	ItemID          int64   `json:"item_id"`
	ItemName        string  `json:"item_name"`
	QuantityOnHand  int     `json:"quantity_on_hand"`
	SellingPrice    float64 `json:"selling_price"`
	CostPerBottle   float64 `json:"cost_per_bottle"`
	TotalItemCost   float64 `json:"total_item_cost"`
	TotalItemValue  float64 `json:"total_item_value"`
	UnitsSold       int     `json:"units_sold"`
	Revenue         float64 `json:"revenue"`
}

// ─── Store ────────────────────────────────────────────────────────────────────

type Store struct {
	db *sql.DB
}

func InitDB(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}

	// Enable WAL mode and foreign keys
	pragmas := []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA foreign_keys=ON",
	}
	for _, p := range pragmas {
		if _, err := db.Exec(p); err != nil {
			return nil, fmt.Errorf("pragma %q: %w", p, err)
		}
	}

	if err := migrate(db); err != nil {
		return nil, fmt.Errorf("migrate: %w", err)
	}

	return &Store{db: db}, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func migrate(db *sql.DB) error {
	schema := `
	CREATE TABLE IF NOT EXISTS inventory_items (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		name TEXT NOT NULL,
		description TEXT DEFAULT '',
		unit TEXT NOT NULL DEFAULT 'bottle',
		quantity_on_hand INTEGER NOT NULL DEFAULT 0,
		selling_price REAL NOT NULL DEFAULT 0,
		created_at TEXT NOT NULL DEFAULT (datetime('now')),
		updated_at TEXT NOT NULL DEFAULT (datetime('now'))
	);

	CREATE TABLE IF NOT EXISTS ingredients (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
		name TEXT NOT NULL,
		cost_per_unit REAL NOT NULL,
		unit TEXT NOT NULL,
		quantity_per_bottle REAL NOT NULL,
		created_at TEXT NOT NULL DEFAULT (datetime('now')),
		updated_at TEXT NOT NULL DEFAULT (datetime('now'))
	);

	CREATE TABLE IF NOT EXISTS sales (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id),
		quantity INTEGER NOT NULL,
		sale_price REAL NOT NULL,
		sale_date TEXT NOT NULL DEFAULT (date('now')),
		notes TEXT DEFAULT '',
		created_at TEXT NOT NULL DEFAULT (datetime('now'))
	);
	`
	_, err := db.Exec(schema)
	return err
}

// ─── Inventory Items ─────────────────────────────────────────────────────────

func (s *Store) CreateItem(name, description, unit string, qty int, price float64) (int64, error) {
	now := time.Now().UTC().Format(time.RFC3339)
	res, err := s.db.Exec(
		`INSERT INTO inventory_items (name, description, unit, quantity_on_hand, selling_price, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		name, description, unit, qty, price, now, now,
	)
	if err != nil {
		return 0, fmt.Errorf("create item: %w", err)
	}
	return res.LastInsertId()
}

func (s *Store) GetItem(id int64) (*InventoryItem, error) {
	item := &InventoryItem{}
	err := s.db.QueryRow(
		`SELECT id, name, description, unit, quantity_on_hand, selling_price, created_at, updated_at
		 FROM inventory_items WHERE id = ?`, id,
	).Scan(&item.ID, &item.Name, &item.Description, &item.Unit, &item.QuantityOnHand, &item.SellingPrice, &item.CreatedAt, &item.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("get item %d: %w", id, err)
	}
	return item, nil
}

func (s *Store) ListItems() ([]InventoryItem, error) {
	rows, err := s.db.Query(
		`SELECT id, name, description, unit, quantity_on_hand, selling_price, created_at, updated_at
		 FROM inventory_items ORDER BY name`)
	if err != nil {
		return nil, fmt.Errorf("list items: %w", err)
	}
	defer rows.Close()

	var items []InventoryItem
	for rows.Next() {
		var it InventoryItem
		if err := rows.Scan(&it.ID, &it.Name, &it.Description, &it.Unit, &it.QuantityOnHand, &it.SellingPrice, &it.CreatedAt, &it.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan item: %w", err)
		}
		items = append(items, it)
	}
	return items, rows.Err()
}

func (s *Store) UpdateItem(id int64, name, description, unit string, qty int, price float64) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := s.db.Exec(
		`UPDATE inventory_items SET name=?, description=?, unit=?, quantity_on_hand=?, selling_price=?, updated_at=?
		 WHERE id=?`,
		name, description, unit, qty, price, now, id,
	)
	if err != nil {
		return fmt.Errorf("update item %d: %w", id, err)
	}
	return nil
}

func (s *Store) DeleteItem(id int64) error {
	_, err := s.db.Exec(`DELETE FROM inventory_items WHERE id=?`, id)
	if err != nil {
		return fmt.Errorf("delete item %d: %w", id, err)
	}
	return nil
}

// ─── Ingredients ──────────────────────────────────────────────────────────────

func (s *Store) CreateIngredient(itemID int64, name, unit string, costPerUnit, qtyPerBottle float64) (int64, error) {
	now := time.Now().UTC().Format(time.RFC3339)
	res, err := s.db.Exec(
		`INSERT INTO ingredients (inventory_item_id, name, cost_per_unit, unit, quantity_per_bottle, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		itemID, name, costPerUnit, unit, qtyPerBottle, now, now,
	)
	if err != nil {
		return 0, fmt.Errorf("create ingredient: %w", err)
	}
	return res.LastInsertId()
}

func (s *Store) GetIngredient(id int64) (*Ingredient, error) {
	ing := &Ingredient{}
	err := s.db.QueryRow(
		`SELECT id, inventory_item_id, name, cost_per_unit, unit, quantity_per_bottle, created_at, updated_at
		 FROM ingredients WHERE id = ?`, id,
	).Scan(&ing.ID, &ing.InventoryItemID, &ing.Name, &ing.CostPerUnit, &ing.Unit, &ing.QuantityPerBottle, &ing.CreatedAt, &ing.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("get ingredient %d: %w", id, err)
	}
	return ing, nil
}

func (s *Store) ListIngredients(itemID int64) ([]Ingredient, error) {
	rows, err := s.db.Query(
		`SELECT id, inventory_item_id, name, cost_per_unit, unit, quantity_per_bottle, created_at, updated_at
		 FROM ingredients WHERE inventory_item_id = ? ORDER BY name`, itemID,
	)
	if err != nil {
		return nil, fmt.Errorf("list ingredients: %w", err)
	}
	defer rows.Close()

	var ings []Ingredient
	for rows.Next() {
		var ing Ingredient
		if err := rows.Scan(&ing.ID, &ing.InventoryItemID, &ing.Name, &ing.CostPerUnit, &ing.Unit, &ing.QuantityPerBottle, &ing.CreatedAt, &ing.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan ingredient: %w", err)
		}
		ings = append(ings, ing)
	}
	return ings, rows.Err()
}

func (s *Store) UpdateIngredient(id int64, name, unit string, costPerUnit, qtyPerBottle float64) error {
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := s.db.Exec(
		`UPDATE ingredients SET name=?, cost_per_unit=?, unit=?, quantity_per_bottle=?, updated_at=?
		 WHERE id=?`,
		name, costPerUnit, unit, qtyPerBottle, now, id,
	)
	if err != nil {
		return fmt.Errorf("update ingredient %d: %w", id, err)
	}
	return nil
}

func (s *Store) DeleteIngredient(id int64) error {
	_, err := s.db.Exec(`DELETE FROM ingredients WHERE id=?`, id)
	if err != nil {
		return fmt.Errorf("delete ingredient %d: %w", id, err)
	}
	return nil
}

// ─── Sales ───────────────────────────────────────────────────────────────────

func (s *Store) CreateSale(itemID int64, qty int, price float64, date, notes string) (int64, error) {
	// Deduct from inventory
	tx, err := s.db.Begin()
	if err != nil {
		return 0, fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	// Check we have enough stock
	var onHand int
	err = tx.QueryRow(`SELECT quantity_on_hand FROM inventory_items WHERE id=?`, itemID).Scan(&onHand)
	if err != nil {
		return 0, fmt.Errorf("get stock for item %d: %w", itemID, err)
	}
	if onHand < qty {
		return 0, fmt.Errorf("insufficient stock: have %d, need %d", onHand, qty)
	}

	now := time.Now().UTC().Format(time.RFC3339)
	res, err := tx.Exec(
		`INSERT INTO sales (inventory_item_id, quantity, sale_price, sale_date, notes, created_at)
		 VALUES (?, ?, ?, ?, ?, ?)`,
		itemID, qty, price, date, notes, now,
	)
	if err != nil {
		return 0, fmt.Errorf("create sale: %w", err)
	}

	// Deduct quantity
	_, err = tx.Exec(
		`UPDATE inventory_items SET quantity_on_hand = quantity_on_hand - ?, updated_at = ? WHERE id=?`,
		qty, now, itemID,
	)
	if err != nil {
		return 0, fmt.Errorf("deduct inventory: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("commit sale: %w", err)
	}

	return res.LastInsertId()
}

func (s *Store) ListSales() ([]Sale, error) {
	rows, err := s.db.Query(
		`SELECT s.id, s.inventory_item_id, i.name, s.quantity, s.sale_price, s.sale_date, s.notes, s.created_at
		 FROM sales s
		 JOIN inventory_items i ON i.id = s.inventory_item_id
		 ORDER BY s.sale_date DESC, s.created_at DESC`)
	if err != nil {
		return nil, fmt.Errorf("list sales: %w", err)
	}
	defer rows.Close()

	var sales []Sale
	for rows.Next() {
		var s Sale
		if err := rows.Scan(&s.ID, &s.InventoryItemID, &s.ItemName, &s.Quantity, &s.SalePrice, &s.SaleDate, &s.Notes, &s.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan sale: %w", err)
		}
		sales = append(sales, s)
	}
	return sales, rows.Err()
}

// ─── Summary / Cost Queries ─────────────────────────────────────────────────

func (s *Store) CostPerItem(itemID int64) (float64, error) {
	var cost sql.NullFloat64
	err := s.db.QueryRow(
		`SELECT SUM(cost_per_unit * quantity_per_bottle)
		 FROM ingredients WHERE inventory_item_id = ?`, itemID,
	).Scan(&cost)
	if err != nil {
		return 0, fmt.Errorf("cost per item %d: %w", itemID, err)
	}
	if cost.Valid {
		return cost.Float64, nil
	}
	return 0, nil
}

func (s *Store) SummaryStats() (*Summary, error) {
	summary := &Summary{}

	// Get all items
	items, err := s.ListItems()
	if err != nil {
		return nil, fmt.Errorf("list items for summary: %w", err)
	}

	for _, item := range items {
		costPerBottle, err := s.CostPerItem(item.ID)
		if err != nil {
			return nil, err
		}

		// Get sales for this item
		var unitsSold int
		var revenue float64
		err = s.db.QueryRow(
			`SELECT COALESCE(SUM(quantity), 0), COALESCE(SUM(quantity * sale_price), 0)
			 FROM sales WHERE inventory_item_id = ?`, item.ID,
		).Scan(&unitsSold, &revenue)
		if err != nil {
			return nil, fmt.Errorf("sales for item %d: %w", item.ID, err)
		}

		totalItemCost := costPerBottle * float64(item.QuantityOnHand)
		totalItemValue := item.SellingPrice * float64(item.QuantityOnHand)

		si := SummaryItem{
			ItemID:         item.ID,
			ItemName:       item.Name,
			QuantityOnHand: item.QuantityOnHand,
			SellingPrice:   item.SellingPrice,
			CostPerBottle:  costPerBottle,
			TotalItemCost:  totalItemCost,
			TotalItemValue: totalItemValue,
			UnitsSold:      unitsSold,
			Revenue:        revenue,
		}
		summary.Items = append(summary.Items, si)
		summary.TotalValue += totalItemValue
		summary.TotalCost += totalItemCost
		summary.TotalSales += revenue
		summary.TotalBottles += item.QuantityOnHand
	}

	summary.GrossProfit = summary.TotalSales - summary.TotalCost

	return summary, nil
}
