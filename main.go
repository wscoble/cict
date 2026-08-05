package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
)

func main() {
	dbPath := os.Getenv("CICT_DB_PATH")
	if dbPath == "" {
		dbPath = "cict.db"
	}

	store, err := InitDB(dbPath)
	if err != nil {
		log.Fatalf("init db: %v", err)
	}
	defer store.Close()

	mux := http.NewServeMux()

	// Dashboard
	mux.HandleFunc("GET /", store.handleHome)

	// Inventory
	mux.HandleFunc("GET /inventory", store.handleInventoryList)
	mux.HandleFunc("GET /inventory/new", store.handleInventoryNew)
	mux.HandleFunc("POST /inventory", store.handleInventoryCreate)
	mux.HandleFunc("GET /inventory/{id}", store.handleInventoryDetail)
	mux.HandleFunc("GET /inventory/{id}/edit", store.handleInventoryEdit)
	mux.HandleFunc("PUT /inventory/{id}", store.handleInventoryUpdate)
	mux.HandleFunc("DELETE /inventory/{id}", store.handleInventoryDelete)

	// Ingredients
	mux.HandleFunc("GET /inventory/{id}/ingredients/new", store.handleIngredientNew)
	mux.HandleFunc("POST /inventory/{id}/ingredients", store.handleIngredientCreate)
	mux.HandleFunc("GET /inventory/{id}/ingredients/{ingId}/edit", store.handleIngredientEdit)
	mux.HandleFunc("PUT /inventory/{id}/ingredients/{ingId}", store.handleIngredientUpdate)
	mux.HandleFunc("DELETE /inventory/{id}/ingredients/{ingId}", store.handleIngredientDelete)

	// Sales
	mux.HandleFunc("GET /sales", store.handleSalesList)
	mux.HandleFunc("GET /sales/new", store.handleSalesNew)
	mux.HandleFunc("POST /sales", store.handleSalesCreate)

	// Summary
	mux.HandleFunc("GET /summary", store.handleSummary)

	// Wrap with method override middleware (converts POST with _method=PUT/DELETE)
	handler := methodOverrideMiddleware(mux)

	addr := ":8080"
	fmt.Printf("cict listening on %s\n", addr)
	log.Fatal(http.ListenAndServe(addr, handler))
}

// methodOverrideMiddleware checks for _method field in POST form data
// and rewrites the request method for browsers that don't support PUT/DELETE.
func methodOverrideMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" {
			if err := r.ParseForm(); err == nil {
				if m := r.FormValue("_method"); m == "PUT" || m == "DELETE" {
					r.Method = m
				}
			}
		}
		next.ServeHTTP(w, r)
	})
}
