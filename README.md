# Slack RAY Translator App

The official [Slack App](https://api.slack.com/) for the Straker Translations RAY platform. This app allows clients to view and manage their translation jobs from Slack.

## Development
### Requirements
- [Python 3.10](https://www.python.org/)
- [Pipenv](https://pipenv.pypa.io/)
- MySQL

### Quick Set Up
1. Create a Pipenv virtual environment and install dependencies using

   ```bash
   $ pipenv install
   ```

2. Set up environment variables by copying the `.env.example` to `.env` and writing the configuration for your app.

   ```bash
   $ mv .env.example .env
   ```

   The `.env` file contains sensitive and device-specific information so is not committed to the repository.

3. Download the credentials of your IBM Watson Assistant service instance and place the file (named `ibm-credentials.env`) in the root directory.

   You can find the service instance in your [resource list](https://cloud.ibm.com/resources) under **Services and software**, the product should be **Watson Assistant**. After opening the service instance page, click the *Download* button in the *Credentials* section.

4. Start the app using

   ```bash
   $ pipenv run uvicorn app.main:app --reload
   ```

   *Optionally specify a port by adding `--port <int>`, e.g.*

   ```bash
   $ pipenv run uvicorn app.main:app --reload --port 3000
   ```

   The app should now be running at `localhost` in the port you specified, e.g. http://localhost:8000.

5. To allow the Slack API to communicate with your app, the URL must be exposed to the public. You can do this with [boringproxy](https://boringproxy.io/), [ngrok](https://ngrok.com/) or whichever method you choose.

6. After your Slack App is [set up in Slack](https://api.slack.com/apps), set the URLs to point to your app:

   - Redirect URLs (Features -> OAuth & Permissions)
     - `<your-domain>/slack/oauth_redirect`
   - Event Request URL (Features -> Event Subscriptions)
     - `<your-domain>/slack/events`
   - Interactivity Request URL (Features -> Interactivity & Shortcuts)
     - `<your-domain>/slack/events`
   - Slash Commands (Features -> Slash Commands)
     - Point all your request URLS to `<your-domain>/slack/events`

7. Install your Slack App to Slack Workspace with the URL `/slack/install`, e.g. `<your-domain>/slack/install`.

   You can share this URL or embed it in a link on a web page for anyone to install (must have public distribution enabled, Settings -> Manage Distribution). **Make sure the app is secure before making it available to the public.**

### Troubleshooting
#### **ModuleNotFoundError: No module named '...'**
This means that some Python modules (dependencies) are not installed. To fix this, install the dependencies by running

```bash
$ pipenv install
```

This will create a virtual enviroment and install the dependencies from `Pipfile`.

#### **[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate**
If you are using macOS and installed Python directly from https://www.python.org, you may get an error like this when installing the app to Slack:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:997)
```

To fix this, install the SSL certificates by running the script at `/Applications/Python\ 3.10/Install\ Certificates.command` (or just double clicking the `Install Certificates.command` file in the `Applications/Python 3.10` directory), then restart the app.
