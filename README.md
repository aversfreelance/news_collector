# Pest Megyei Hírlap – külön hírgyűjtő repo

Ez külön GitHub repositoryba való, nem a Lovable portál repo-jába.

## Működés

1. GitHub Actions → **Collect Pest County news draft** → Run workflow
2. Elkészül: `public/import/pest-megye-news-draft.json`
3. Ellenőrzés után GitHub Actions → **Promote Pest County news draft to live** → Run workflow
4. Éles fájl: `public/import/pest-megye-news.json`

## Fontos

- Nem élesít automatikusan.
- A duplikációkat URL, azonos cím és hasonló cím alapján szűri.
- Az `image` mező csak valódi kép URL lehet. Ha nincs kép, üres: `"image": ""`.
- A portál a külön repo nyers JSON URL-jéből vagy GitHub Pages/Cloudflare Pages URL-ről tud importálni.

## Városok módosítása

A források itt vannak:

`config/city_sources.json`

Bármikor bővíthető vagy javítható.
