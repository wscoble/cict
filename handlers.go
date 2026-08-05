package main

import (
	"fmt"
	"log"
	"net/http"
	"strconv"
	"time"
)

// ─── Page Data Helpers ───────────────────────────────────────────────────────

type pageData struct {
	Title string
	Data  any
}

func pd(title string, data any) pageData {
	return pageData{Title: title, Data: data}
}

// ─── Home ─────────────────────────────────────────────────────────────────────

func (s *Store) handleHome(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}

	summary, err := s.SummaryStats()
	if err != nil {
		log.Printf("home summary: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// Count sales this month
	monthStart := time.Now().Format("2006-01")
	var recentSales int
	err = s.db.QueryRow(
		`SELECT COUNT(*) FROM sales WHERE sale_date >= ?`, monthStart+"-01",
	).Scan(&recentSales)
	if err != nil {
		log.Printf("recent sales count: %v", err)
		recentSales = 0
	}

	data := struct {
		ItemCount           int
		TotalBottles        int
		RecentSales         int
		TotalInventoryValue float64
	}{
		ItemCount:           len(summary.Items),
		TotalBottles:        summary.TotalBottles,
		RecentSales:         recentSales,
		TotalInventoryValue: summary.TotalCost,
	}

	renderPage(w, r, "home.html", pd("Dashboard", data))
}

// ─── Inventory List ──────────────────────────────────────────────────────────

func (s *Store) handleInventoryList(w http.ResponseWriter, r *http.Request) {
	items, err := s.ListItems()
	if err != nil {
		log.Printf("list items: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// Enrich with cost per bottle
	type itemRow struct {
		InventoryItem
		CostPerBottle float64
	}
	var rows []itemRow
	for _, it := range items {
		cost, _ := s.CostPerItem(it.ID)
		rows = append(rows, itemRow{InventoryItem: it, CostPerBottle: cost})
	}

	data := struct {
		Items []itemRow
	}{Items: rows}

	renderPage(w, r, "inventory_list.html", pd("Inventory", data))
}

// ─── Inventory New (form) ───────────────────────────────────────────────────

func (s *Store) handleInventoryNew(w http.ResponseWriter, r *http.Request) {
	renderPage(w, r, "inventory_form.html", pd("New Item", nil))
}

// ─── Inventory Create ────────────────────────────────────────────────────────

func (s *Store) handleInventoryCreate(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	name := r.FormValue("name")
	desc := r.FormValue("description")
	unit := r.FormValue("unit")
	qty, _ := strconv.Atoi(r.FormValue("quantity_on_hand"))
	price, _ := strconv.ParseFloat(r.FormValue("selling_price"), 64)

	if name == "" || unit == "" {
		http.Error(w, "name and unit are required", http.StatusBadRequest)
		return
	}

	id, err := s.CreateItem(name, desc, unit, qty, price)
	if err != nil {
		log.Printf("create item: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, fmt.Sprintf("/inventory/%d", id), http.StatusSeeOther)
}

// ─── Inventory Detail ───────────────────────────────────────────────────────

func (s *Store) handleInventoryDetail(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	item, err := s.GetItem(id)
	if err != nil {
		log.Printf("get item %d: %v", id, err)
		http.NotFound(w, r)
		return
	}

	ingredients, err := s.ListIngredients(id)
	if err != nil {
		log.Printf("list ingredients %d: %v", id, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	costPerBottle, _ := s.CostPerItem(id)

	// Enrich ingredients with line cost
	type ingRow struct {
		Ingredient
		LineCost float64
	}
	var ings []ingRow
	for _, ing := range ingredients {
		ings = append(ings, ingRow{Ingredient: ing, LineCost: ing.CostPerUnit * ing.QuantityPerBottle})
	}

	margin := item.SellingPrice - costPerBottle
	var marginPercent float64
	if item.SellingPrice > 0 {
		marginPercent = (margin / item.SellingPrice) * 100
	}

	data := struct {
		Item          *InventoryItem
		Ingredients   []ingRow
		CostPerBottle float64
		Margin        float64
		MarginPercent float64
	}{
		Item:          item,
		Ingredients:   ings,
		CostPerBottle: costPerBottle,
		Margin:        margin,
		MarginPercent: marginPercent,
	}

	renderPage(w, r, "inventory_detail.html", pd(item.Name, data))
}

// ─── Inventory Edit (form) ──────────────────────────────────────────────────

func (s *Store) handleInventoryEdit(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	item, err := s.GetItem(id)
	if err != nil {
		log.Printf("get item %d: %v", id, err)
		http.NotFound(w, r)
		return
	}

	data := struct {
		Item *InventoryItem
	}{Item: item}

	renderPage(w, r, "inventory_form.html", pd("Edit "+item.Name, data))
}

// ─── Inventory Update ───────────────────────────────────────────────────────

func (s *Store) handleInventoryUpdate(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	name := r.FormValue("name")
	desc := r.FormValue("description")
	unit := r.FormValue("unit")
	qty, _ := strconv.Atoi(r.FormValue("quantity_on_hand"))
	price, _ := strconv.ParseFloat(r.FormValue("selling_price"), 64)

	if name == "" || unit == "" {
		http.Error(w, "name and unit are required", http.StatusBadRequest)
		return
	}

	if err := s.UpdateItem(id, name, desc, unit, qty, price); err != nil {
		log.Printf("update item %d: %v", id, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, fmt.Sprintf("/inventory/%d", id), http.StatusSeeOther)
}

// ─── Inventory Delete ───────────────────────────────────────────────────────

func (s *Store) handleInventoryDelete(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	if err := s.DeleteItem(id); err != nil {
		log.Printf("delete item %d: %v", id, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, "/inventory", http.StatusSeeOther)
}

// ─── Ingredient New (form) ──────────────────────────────────────────────────

func (s *Store) handleIngredientNew(w http.ResponseWriter, r *http.Request) {
	itemID, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	item, err := s.GetItem(itemID)
	if err != nil {
		log.Printf("get item %d: %v", itemID, err)
		http.NotFound(w, r)
		return
	}

	data := struct {
		ItemID     int64
		ItemName   string
		ItemUnit   string
		Ingredient *Ingredient
	}{ItemID: itemID, ItemName: item.Name, ItemUnit: item.Unit, Ingredient: nil}

	renderPage(w, r, "ingredient_form.html", pd("Add Ingredient", data))
}

// ─── Ingredient Create ──────────────────────────────────────────────────────

func (s *Store) handleIngredientCreate(w http.ResponseWriter, r *http.Request) {
	itemID, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	name := r.FormValue("name")
	unit := r.FormValue("unit")
	costPerUnit, _ := strconv.ParseFloat(r.FormValue("cost_per_unit"), 64)
	qtyPerBottle, _ := strconv.ParseFloat(r.FormValue("quantity_per_bottle"), 64)

	if name == "" || unit == "" {
		http.Error(w, "name and unit are required", http.StatusBadRequest)
		return
	}

	_, err = s.CreateIngredient(itemID, name, unit, costPerUnit, qtyPerBottle)
	if err != nil {
		log.Printf("create ingredient: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, fmt.Sprintf("/inventory/%d", itemID), http.StatusSeeOther)
}

// ─── Ingredient Edit (form) ────────────────────────────────────────────────

func (s *Store) handleIngredientEdit(w http.ResponseWriter, r *http.Request) {
	itemID, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	ingID, err := strconv.ParseInt(r.PathValue("ingId"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	item, err := s.GetItem(itemID)
	if err != nil {
		log.Printf("get item %d: %v", itemID, err)
		http.NotFound(w, r)
		return
	}

	ing, err := s.GetIngredient(ingID)
	if err != nil {
		log.Printf("get ingredient %d: %v", ingID, err)
		http.NotFound(w, r)
		return
	}

	data := struct {
		ItemID     int64
		ItemName   string
		ItemUnit   string
		Ingredient *Ingredient
	}{
		ItemID:     itemID,
		ItemName:   item.Name,
		ItemUnit:   item.Unit,
		Ingredient: ing,
	}

	renderPage(w, r, "ingredient_form.html", pd("Edit Ingredient", data))
}

// ─── Ingredient Update ──────────────────────────────────────────────────────

func (s *Store) handleIngredientUpdate(w http.ResponseWriter, r *http.Request) {
	itemID, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	ingID, err := strconv.ParseInt(r.PathValue("ingId"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	name := r.FormValue("name")
	unit := r.FormValue("unit")
	costPerUnit, _ := strconv.ParseFloat(r.FormValue("cost_per_unit"), 64)
	qtyPerBottle, _ := strconv.ParseFloat(r.FormValue("quantity_per_bottle"), 64)

	if name == "" || unit == "" {
		http.Error(w, "name and unit are required", http.StatusBadRequest)
		return
	}

	if err := s.UpdateIngredient(ingID, name, unit, costPerUnit, qtyPerBottle); err != nil {
		log.Printf("update ingredient %d: %v", ingID, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, fmt.Sprintf("/inventory/%d", itemID), http.StatusSeeOther)
}

// ─── Ingredient Delete ──────────────────────────────────────────────────────

func (s *Store) handleIngredientDelete(w http.ResponseWriter, r *http.Request) {
	itemID, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	ingID, err := strconv.ParseInt(r.PathValue("ingId"), 10, 64)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	if err := s.DeleteIngredient(ingID); err != nil {
		log.Printf("delete ingredient %d: %v", ingID, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, fmt.Sprintf("/inventory/%d", itemID), http.StatusSeeOther)
}

// ─── Sales List ─────────────────────────────────────────────────────────────

func (s *Store) handleSalesList(w http.ResponseWriter, r *http.Request) {
	sales, err := s.ListSales()
	if err != nil {
		log.Printf("list sales: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// Enrich with revenue
	type saleRow struct {
		Sale
		Revenue float64
	}
	var rows []saleRow
	for _, sale := range sales {
		rows = append(rows, saleRow{Sale: sale, Revenue: float64(sale.Quantity) * sale.SalePrice})
	}

	data := struct {
		Sales []saleRow
	}{Sales: rows}

	renderPage(w, r, "sales_list.html", pd("Sales", data))
}

// ─── Sales New (form) ───────────────────────────────────────────────────────

func (s *Store) handleSalesNew(w http.ResponseWriter, r *http.Request) {
	items, err := s.ListItems()
	if err != nil {
		log.Printf("list items for sale form: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	today := time.Now().Format("2006-01-02")

	data := struct {
		Items []InventoryItem
		Today string
	}{Items: items, Today: today}

	renderPage(w, r, "sales_form.html", pd("Record Sale", data))
}

// ─── Sales Create ───────────────────────────────────────────────────────────

func (s *Store) handleSalesCreate(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	itemID, _ := strconv.ParseInt(r.FormValue("inventory_item_id"), 10, 64)
	qty, _ := strconv.Atoi(r.FormValue("quantity"))
	price, _ := strconv.ParseFloat(r.FormValue("sale_price"), 64)
	date := r.FormValue("sale_date")
	notes := r.FormValue("notes")

	if itemID == 0 || qty <= 0 {
		http.Error(w, "item and quantity are required", http.StatusBadRequest)
		return
	}

	if date == "" {
		date = time.Now().Format("2006-01-02")
	}

	_, err := s.CreateSale(itemID, qty, price, date, notes)
	if err != nil {
		log.Printf("create sale: %v", err)
		// Check for insufficient stock
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	http.Redirect(w, r, "/sales", http.StatusSeeOther)
}

// ─── Summary ────────────────────────────────────────────────────────────────

func (s *Store) handleSummary(w http.ResponseWriter, r *http.Request) {
	summary, err := s.SummaryStats()
	if err != nil {
		log.Printf("summary: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	renderPage(w, r, "summary.html", pd("Cost Summary", summary))
}
