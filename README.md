# Discord Rich Presence for Plex

A Python script that displays your [Plex](https://www.plex.tv) status on [Discord](https://discordapp.com) using [Rich Presence](https://discordapp.com/developers/docs/rich-presence/how-to).

## Requirements

* [Python 3.6.7](https://www.python.org/downloads/release/python-367/)
* [plexapi](https://github.com/pkkid/python-plexapi)
* Use [websocket-client](https://github.com/websocket-client/websocket-client) version 0.48.0 (`pip install websocket-client==0.48.0`) as an issue with newer versions breaks the plexapi module's alert listener.
* The script must be running on the same machine as your Discord client.

## Configuration

Create a config.json file in the same directory as the python file.

#### Example

```json
{
  "server_name": "myserver",
  "username": "myuser",
  "password": "mypass"
}
``` 

#### Parameters

* `server_name` - Name of the Plex Media Server to connect to.
* `username` - Your account's username or e-mail.
* `password` (not required if `token` is set) - The password associated with the above account.
* `token` (not required if `password` is set) - A [X-Plex-Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token) associated with the above account. In some cases, `myPlexAccessToken` from Plex Web App's HTML5 Local Storage must be used. To retrieve this token in Google Chrome, open Plex Web App, press F12, go to "Application", expand "Local Storage" and select the relevant entry. Ignores `password` if set.
* `listen_for_user` (optional) - The script will respond to alerts originating only from this username. Defaults to `username` if not set.
* `blacklisted_libraries` (list, optional) - Alerts originating from blacklisted libraries are ignored.
* `whitelisted_libraries` (list, optional) - If set, alerts originating from libraries that are not in the whitelist are ignored.

### Other Variables

* Line 16: `extra_logging` - The script outputs more information if this is set to `True`.
* Line 17: `time_remaining` - Set this to `True` to display time remaining instead of time elapsed while media is playing.

## License

This project is licensed under the MIT License. See the [LICENSE](https://github.com/Phineas05/discord-rich-presence-plex/blob/master/LICENSE) file for details.
