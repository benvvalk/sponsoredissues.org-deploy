# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Django 5.2.3 project named "sponsoredissues" - a fresh Django installation with the standard project structure.

## Architecture

- **Framework**: Django 5.2.3
- **Database**: SQLite (db.sqlite3)
- **Project Structure**: Standard Django layout with main project in `sponsoredissues/` directory
- **Apps**: Currently only has Django's built-in admin interface enabled

## Development Commands

### Running the Development Server
```bash
python manage.py runserver
```

### Database Operations
```bash
# Apply migrations
python manage.py migrate

# Create migrations after model changes
python manage.py makemigrations

# Create superuser for admin access
python manage.py createsuperuser
```

### Django Management
```bash
# Access Django shell
python manage.py shell

# Collect static files (when needed)
python manage.py collectstatic
```

## Project Configuration

- **Settings**: Located in `sponsoredissues/settings.py`
- **URLs**: Main URL configuration in `sponsoredissues/urls.py`
- **Database**: SQLite database file at project root (`db.sqlite3`)
- **Admin Interface**: Available at `/admin/` endpoint

## Key Files

- `manage.py`: Django's command-line utility
- `sponsoredissues/settings.py`: Main Django settings
- `sponsoredissues/urls.py`: URL routing configuration
- `sponsoredissues/wsgi.py`: WSGI application entry point
- `sponsoredissues/asgi.py`: ASGI application entry point