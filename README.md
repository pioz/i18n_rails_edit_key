# I18n Rails Edit Key

A Sublime Text plugin to simplify managing **I18n Rails translations** directly from your editor.

## Disclaimer ⚠️

This plugin was completely generated and written by ChatGPT 5 using the ["vibe coding"](https://en.wikipedia.org/wiki/Vibe_coding) technique.

## Features

- **Edit Key Values**
  - Works only on calls like `t('...')` or `I18n.t('...')`.
  - Supports both **absolute** (`"users.show.title"`) and **relative** keys (`".title"`).
  - Resolves relative keys automatically:
    - In views: `app/views/users/show.html.erb` + `.title` → `users.show.title`
    - In controllers: `app/controllers/users_controller.rb` inside `def update` + `.success` → `users.update.success`
  - Reads current translations from all matching files in `config/locales/`.
  - Prompts you for each locale (`en.yml`, `it.yml`, …) with pre-filled values.
  - Saves all changes into the **primary locale file** (`<locale>.yml`).

- **Jump To Key**
  - Works only on calls like `t('...')` or `I18n.t('...')`.
  - Asks which locale to use.
  - Opens the corresponding `<locale>.yml`.
  - Navigates to the value of the selected key.

- **Shortcuts & Menus**
  - Default key bindings:
    - macOS: `⌘ + ⌥ + e` (Edit), `⌘ + ⌥ + j` (Jump)
    - Windows/Linux: `Ctrl + Alt + e` (Edit), `Ctrl + Alt + j` (Jump)
  - Command Palette entries:
    - `I18n Rails: Edit Key Values`
    - `I18n Rails: Jump To Key`
  - Context menu entries (right click in a Rails file).

## Installation

1. Open Sublime Text and go to **Preferences → Browse Packages…**
2. Clone this repository directly into `Packages` directory
```
git clone https://github.com/pioz/i18n_rails_edit_key.git I18nRailsHelper

````

## Settings

Edit `I18nRailsEditKey.sublime-settings` to customize:

```json
{
  "locales_dir": "config/locales", // relative to project root
  "default_locale_first": "en",    // which locale is prompted first
  "ruby_path": "ruby"              // path to Ruby binary
}
````

## Usage

* Place the cursor inside a `t('...')` call.
* Run **Edit Key Values** (`⌘/Ctrl + Alt + e`) → enter translations for each locale.
* Or run **Jump To Key** (`⌘/Ctrl + Alt + j`) → pick a locale and jump to the translation in YAML.

## Requirements

* Ruby available in PATH (or specify `"ruby_path"` in settings).
* Rails-style project with `config/locales/*.yml` files.

## License

MIT
