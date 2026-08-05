package main

import (
	"embed"
	"fmt"
	"html/template"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
)

//go:embed templates/*
var templateFS embed.FS

// _pageTmpls caches one *template.Template per page: base.html + that page
// only. Parsing base + a single page means each page's "content" block is the
// only one in the set, so {{define "content"}} blocks from different pages
// never collide (which is what happened when all pages were parsed together:
// the alphabetically-last page's content block silently overrode the rest).
var _pageTmpls = map[string]*template.Template{}
var _pageMu sync.Mutex

// _partialTmpls holds all page templates parsed together, for partial (htmx)
// renders that execute a named template directly. Partial renders don't use
// the "content" block, so the collision is harmless here.
var _partialTmpls *template.Template

func init() {
	var err error
	_partialTmpls, err = template.ParseFS(templateFS, "templates/*.html")
	if err != nil {
		log.Fatalf("parse partial templates: %v", err)
	}
}

// pageTmpl returns (and caches) a template set containing base.html + the
// named page, so the page's "content" block is unique within the set.
func pageTmpl(name string) (*template.Template, error) {
	_pageMu.Lock()
	defer _pageMu.Unlock()
	if t, ok := _pageTmpls[name]; ok {
		return t, nil
	}
	t, err := template.ParseFS(templateFS, "templates/base.html", "templates/"+name)
	if err != nil {
		return nil, fmt.Errorf("parse page %q: %w", name, err)
	}
	_pageTmpls[name] = t
	return t, nil
}

func isHtmxRequest(r *http.Request) bool {
	return r.Header.Get("HX-Request") == "true"
}

func renderPage(w http.ResponseWriter, r *http.Request, name string, data any) {
	// hx-boost on <body> fetches the full page and swaps the body itself, so we
	// always return the full document. (A broken partial path used to return a
	// 1-byte empty response on boosted clicks, blanking the page.)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	t, err := pageTmpl(name)
	if err != nil {
		log.Printf("load page %q: %v", name, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if err := t.ExecuteTemplate(w, "base.html", data); err != nil {
		log.Printf("render page %q: %v", name, err)
	}
}

func renderPartial(w http.ResponseWriter, name string, data any) {
	// Explicit hx-get fragment: render just the page's content block. Uses the
	// per-page template set (pageTmpl) so the "content" block is the right one.
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	t, err := pageTmpl(name)
	if err != nil {
		log.Printf("load partial %q: %v", name, err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if err := t.ExecuteTemplate(w, "content", data); err != nil {
		log.Printf("render partial %q: %v", name, err)
	}
}

// renderString executes a template and returns the result as a string.
func renderString(name string, data any) (string, error) {
	var buf strings.Builder
	if err := _partialTmpls.ExecuteTemplate(io.Writer(&buf), name, data); err != nil {
		return "", fmt.Errorf("render string %q: %w", name, err)
	}
	return buf.String(), nil
}