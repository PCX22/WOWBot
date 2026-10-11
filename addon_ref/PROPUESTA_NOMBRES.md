# Propuesta: guardar el nombre de cada receta en GuildRecipesDB

**El problema:**
- El addon guarda los **IDs** de las recetas:
  - `members[GUID].p[skillLine].k`: las que conoce cada miembro.
  - `cache.catalog[skillLine]`: todas las que se han visto en la ventana de profesión.
- Los IDs salen del propio juego, así que las recetas nuevas de WoW Forever se registran solas.
- Pero **no guarda el nombre**, y para las recetas nuevas no existirá ninguna base de datos de donde sacarlo. Sin el nombre, el bot solo puede mostrar «Receta #12345».

**La solución:** el addon apunta el nombre cuando ve la receta, en el idioma del cliente, igual que ya hace con las categorías (`cache.cats`):

```lua
cache.names = { [locale] = { [recipeID] = "Nombre de la receta" } }
```

El bot ya lo lee (`bot/importers/guildrecipes.py`, `read_names`): primero `esES`/`esMX` y, si no hay, el idioma que sea. Cada PC solo conoce los nombres de las profesiones que ha abierto, pero el bot junta los de todos los archivos que le llegan.

## Cambios en `Data.lua`

```lua
-- NewDB: añadir names al cache
local function NewDB()
    return { schema = Data.SCHEMA, guilds = {}, cache = { recCat = {}, cats = {}, catalog = {}, names = {} }, settings = {}, own = {} }
end
```

En `Data.Sanitize`, detrás del bloque de `cache.cats`:

```lua
    if type(cache.names) == "table" then
        for locale, names in pairs(cache.names) do
            if type(locale) == "string" and type(names) == "table" then
                local cleanNames = {}
                for recipeID, name in pairs(names) do
                    if IsID(recipeID) then
                        cleanNames[recipeID] = Data.CleanName(name)
                    end
                end
                clean.cache.names[locale] = cleanNames
            end
        end
    end
```

En `Data.MergeDB`, detrás del bucle de `source.cache.cats`:

```lua
    for locale, names in pairs(source.cache.names) do
        local into = target.cache.names[locale]
        if not into then
            target.cache.names[locale] = names
        else
            for recipeID, name in pairs(names) do
                into[recipeID] = into[recipeID] or name
            end
        end
    end
```

Y una función nueva, junto a `Data.SetCatalog`:

```lua
-- The name of a recipe as the client shows it, kept per locale like the categories.
function Data.RememberName(recipeID, name)
    name = Data.CleanName(name)
    if not IsID(recipeID) or not name then
        return
    end
    local locale = GetLocale()
    local names = ns.db.cache.names[locale]
    if not names then
        names = {}
        ns.db.cache.names[locale] = names
    end
    names[recipeID] = name
end
```

## Dónde llamarla
En el código que recorre la ventana de profesión, donde ya se llama a `Data.SetCatalog`. No tengo ese archivo, así que puede que no cuadre exactamente. Por cada receta vista:

```lua
local info = C_TradeSkillUI.GetRecipeInfo(recipeID)
if info and info.name then
    Data.RememberName(recipeID, info.name)
end
```

## Opcional
Para las recetas que crean un objeto, se puede guardar también el ID del objeto. Así el bot podría enlazarlo y buscar por el nombre del objeto. Ejemplo: `C_TradeSkillUI.GetRecipeOutputItemData(recipeID)` (o la función equivalente del cliente de Forever).
