# Channel Info Caching

## Overview

The Home tab displays channel translation settings across the workspace. For private channels where the user is not a member, Slack doesn't display the channel name - only the channel ID. Previously, this required calling the Slack API (`conversations.info`) for each channel when rendering the Home tab, which was inefficient.

This feature stores channel names and privacy status in the database to eliminate excessive API calls.

## How It Works

### Storage

Two new columns were added to the `slack_group_settings_translation` table:

- `channel_name` (VARCHAR 255, nullable) - The name of the Slack channel
- `is_private` (BOOLEAN, default FALSE) - Whether the channel is private

### When Data is Stored

Channel info is stored when:

1. **Creating new translation settings**: When a user creates or updates translation settings via the modal, the channel name and privacy status are captured from the `resolve_channels_to_team` response and stored.

2. **Lazy migration**: For existing settings without channel info (channel_name IS NULL), the Home tab will:
   - Call the Slack API to fetch channel details
   - Update the database with the channel name and privacy status
   - Use the fetched data for display

### Home Tab Display

When rendering the Home tab:
- If `channel_name` is stored, use it directly (no API call)
- If `channel_name` is NULL, fetch from Slack API, update DB, then display

This ensures:
- New settings are immediately cached
- Existing settings are lazily migrated on first view
- Subsequent views use cached data

## Database Migration

Run this SQL to add the new columns:

```sql
ALTER TABLE ray_integration.slack_group_settings_translation
  ADD COLUMN channel_name VARCHAR(255) NULL,
  ADD COLUMN is_private BOOLEAN DEFAULT FALSE NOT NULL;
```

No data migration is required - existing rows will have NULL channel_name and will be lazily updated when viewed.

## Files Modified

- `app/models.py` - Added `channel_name` and `is_private` columns to `SlackGroupSettingsTranslation`
- `app/ray/settings.py` - Updated settings functions to store channel info, added `update_channel_info` function
- `app/slack/listeners.py` - Pass channel info when updating settings
- `app/slack/templates/views.py` - Use stored channel info, lazy update when NULL
