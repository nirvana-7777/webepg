#!/usr/bin/env python3
"""
Channel mapping helper script.

Usage:
    python scripts/map_channels.py list-channels
    python scripts/map_channels.py list-providers
    python scripts/map_channels.py map --provider-id 2 --provider-channel "bbcone" --channel-id 1
    python scripts/map_channels.py import-mappings mappings.yaml
"""
import sys
import argparse
import yaml
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import initialize_db, get_db
from src.services.epg_service import EPGService
from src.services.provider_service import ProviderService
from src.config import load_config


def list_channels():
    """List all logical channels."""
    epg_service = EPGService()
    channels = epg_service.list_channels()

    print("\nLogical Channels:")
    print("=" * 60)
    for channel in channels:
        print(f"ID: {channel.id:3d} | Name: {channel.name:20s} | Display: {channel.display_name}")
    print()


def list_providers():
    """List all providers."""
    provider_service = ProviderService()
    providers = provider_service.list_providers()

    print("\nProviders:")
    print("=" * 60)
    for provider in providers:
        status = "✓ Enabled" if provider.enabled else "✗ Disabled"
        print(f"ID: {provider.id:3d} | {status} | {provider.name}")
    print()


def list_mappings():
    """List all channel mappings."""
    db = get_db()

    rows = db.fetchall("""
                       SELECT cm.id,
                              p.name         as provider_name,
                              cm.provider_channel_id,
                              c.display_name as logical_channel,
                              c.id           as channel_id
                       FROM channel_mappings cm
                                JOIN providers p ON cm.provider_id = p.id
                                JOIN channels c ON cm.channel_id = c.id
                       ORDER BY c.id, p.id
                       """)

    print("\nChannel Mappings:")
    print("=" * 80)
    print(f"{'Provider':<20} | {'Provider Ch ID':<20} | {'→ Logical Channel':<30}")
    print("-" * 80)

    for row in rows:
        print(f"{row[1]:<20} | {row[2]:<20} | → {row[3]} (ID: {row[4]})")
    print()


def list_aliases():
    """List all channel aliases."""
    db = get_db()

    rows = db.fetchall("""
                       SELECT ca.id,
                              c.display_name as channel_name,
                              c.id           as channel_id,
                              ca.alias,
                              ca.alias_type
                       FROM channel_aliases ca
                                JOIN channels c ON ca.channel_id = c.id
                       ORDER BY c.id, ca.alias
                       """)

    print("\nChannel Aliases:")
    print("=" * 80)
    print(f"{'Channel':<30} | {'Alias':<25} | {'Type':<15}")
    print("-" * 80)

    for row in rows:
        alias_type = row[4] or ""
        print(f"{row[1]:<30} | {row[3]:<25} | {alias_type:<15}")
    print()


def create_alias(channel_id: int, alias: str, alias_type: str = None):
    """Create a channel alias."""
    epg_service = EPGService()

    # Verify channel exists
    channel = epg_service.get_channel(channel_id)
    if not channel:
        print(f"Error: Channel {channel_id} not found")
        return False

    try:
        new_alias = epg_service.create_channel_alias(
            channel_id=channel_id,
            alias=alias,
            alias_type=alias_type
        )

        print(f"✓ Created alias:")
        print(f"  Channel: {channel.display_name} (ID: {channel_id})")
        print(f"  Alias: {alias}")
        if alias_type:
            print(f"  Type: {alias_type}")
        return True

    except Exception as e:
        print(f"✗ Error creating alias: {e}")
        return False


def create_mapping(provider_id: int, provider_channel_id: str, channel_id: int):
    """Create a channel mapping."""
    provider_service = ProviderService()
    epg_service = EPGService()

    # Verify provider exists
    provider = provider_service.get_provider(provider_id)
    if not provider:
        print(f"Error: Provider {provider_id} not found")
        return False

    # Verify channel exists
    channel = epg_service.get_channel(channel_id)
    if not channel:
        print(f"Error: Channel {channel_id} not found")
        return False

    try:
        mapping = provider_service.create_channel_mapping(
            provider_id=provider_id,
            provider_channel_id=provider_channel_id,
            channel_id=channel_id
        )

        print(f"✓ Created mapping:")
        print(f"  Provider: {provider.name} (ID: {provider_id})")
        print(f"  Provider Channel: {provider_channel_id}")
        print(f"  → Logical Channel: {channel.display_name} (ID: {channel_id})")
        return True

    except Exception as e:
        print(f"✗ Error creating mapping: {e}")
        return False


def import_mappings_from_yaml(yaml_path: str):
    """Import channel mappings from YAML configuration file."""
    with open(yaml_path, 'r') as f:
        config = yaml.safe_load(f)

    epg_service = EPGService()
    provider_service = ProviderService()

    # Get all providers
    providers = {p.name: p for p in provider_service.list_providers()}

    created_count = 0
    error_count = 0
    alias_count = 0

    for channel_config in config.get('channels', []):
        channel_name = channel_config.get('id')
        display_name = channel_config.get('display_name')
        icon_url = channel_config.get('icon_url')

        # Create or get logical channel
        channel = epg_service.get_or_create_channel(
            name=channel_name,
            display_name=display_name,
            icon_url=icon_url
        )

        print(f"\nChannel: {display_name} (ID: {channel.id})")

        # Create mappings for each provider
        for provider_config in channel_config.get('providers', []):
            provider_name = provider_config.get('provider')
            provider_channel_id = provider_config.get('channel_id')

            if provider_name not in providers:
                print(f"  ✗ Provider '{provider_name}' not found - skipping")
                error_count += 1
                continue

            provider = providers[provider_name]

            # Check if mapping already exists
            existing = provider_service.get_channel_for_provider_channel(
                provider_id=provider.id,
                provider_channel_id=provider_channel_id
            )

            if existing:
                print(f"  → Mapping already exists for {provider_name}:{provider_channel_id}")
                continue

            try:
                provider_service.create_channel_mapping(
                    provider_id=provider.id,
                    provider_channel_id=provider_channel_id,
                    channel_id=channel.id
                )
                print(f"  ✓ Created mapping: {provider_name}:{provider_channel_id}")
                created_count += 1
            except Exception as e:
                print(f"  ✗ Error: {e}")
                error_count += 1

        # Create aliases if specified
        for alias_config in channel_config.get('aliases', []):
            alias = alias_config.get('alias') if isinstance(alias_config, dict) else alias_config
            alias_type = alias_config.get('type') if isinstance(alias_config, dict) else None

            try:
                # Check if alias already exists
                existing_channel = epg_service.get_channel_by_alias(alias)
                if existing_channel:
                    if existing_channel.id == channel.id:
                        print(f"  → Alias '{alias}' already exists")
                    else:
                        print(f"  ✗ Alias '{alias}' already used by another channel")
                        error_count += 1
                    continue

                epg_service.create_channel_alias(
                    channel_id=channel.id,
                    alias=alias,
                    alias_type=alias_type
                )
                print(f"  ✓ Created alias: {alias}")
                alias_count += 1
            except Exception as e:
                print(f"  ✗ Error creating alias '{alias}': {e}")
                error_count += 1

    print(f"\n{'=' * 60}")
    print(f"Summary: {created_count} mappings created, {alias_count} aliases created, {error_count} errors")


def main():
    parser = argparse.ArgumentParser(description='Channel mapping helper')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # List channels command
    subparsers.add_parser('list-channels', help='List all logical channels')

    # List providers command
    subparsers.add_parser('list-providers', help='List all providers')

    # List mappings command
    subparsers.add_parser('list-mappings', help='List all channel mappings')

    # List aliases command
    subparsers.add_parser('list-aliases', help='List all channel aliases')

    # Map command
    map_parser = subparsers.add_parser('map', help='Create a channel mapping')
    map_parser.add_argument('--provider-id', type=int, required=True, help='Provider ID')
    map_parser.add_argument('--provider-channel', required=True, help='Provider channel ID')
    map_parser.add_argument('--channel-id', type=int, required=True, help='Logical channel ID')

    # Create alias command
    alias_parser = subparsers.add_parser('create-alias', help='Create a channel alias')
    alias_parser.add_argument('--channel-id', type=int, required=True, help='Logical channel ID')
    alias_parser.add_argument('--alias', required=True, help='Alias string')
    alias_parser.add_argument('--type', help='Alias type (optional)')

    # Import mappings command
    import_parser = subparsers.add_parser('import-mappings', help='Import mappings from YAML')
    import_parser.add_argument('file', help='YAML file with channel mappings')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Initialize database
    config = load_config()
    db_path = config.get('database.path')
    initialize_db(db_path)

    # Execute command
    if args.command == 'list-channels':
        list_channels()
    elif args.command == 'list-providers':
        list_providers()
    elif args.command == 'list-mappings':
        list_mappings()
    elif args.command == 'list-aliases':
        list_aliases()
    elif args.command == 'map':
        create_mapping(args.provider_id, args.provider_channel, args.channel_id)
    elif args.command == 'create-alias':
        create_alias(args.channel_id, args.alias, args.type)
    elif args.command == 'import-mappings':
        import_mappings_from_yaml(args.file)


if __name__ == '__main__':
    main()