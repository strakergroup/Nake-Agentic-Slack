# Slack RAY Translator App

The official [Slack App](https://api.slack.com/) for the Straker Translations RAY platform. This app allows clients to view and manage their translation jobs from Slack.

## Development
### Requirements
- [Python 3.10](https://www.python.org/)
- MySQL
- [Pipenv](https://pipenv.pypa.io/) (if not using Docker)

### Quick Set Up
1. Set up environment variables by copying the `.env.example` to `.env` and writing the configuration for your app.

   ```bash
   $ cp .env.example .env
   ```

   The `.env` file contains sensitive and device-specific information so is not committed to the repository.

2. Download the credentials of your IBM Watson Assistant service instance and place the file (named `ibm-credentials.env`) in the root directory.

   You can find the service instance in your [resource list](https://cloud.ibm.com/resources) under **Services and software**, the product should be **Watson Assistant**. After opening the service instance page, click the *Download* button in the *Credentials* section.

3. There are 2 ways to run the app: normally with Pipenv or with Docker.
   - ### Without Docker

     Create a Pipenv virtual environment and install the dependencies using

     ```bash
     $ pipenv install
     ```

     Then start the app using

     ```bash
     $ pipenv run uvicorn app.main:app --reload
     ```

     *Optionally specify a port by adding `--port <int>`, e.g.*

     ```bash
     $ pipenv run uvicorn app.main:app --reload --port 3000
     ```

     The `--reload` flag updates the API automatically when there are changes to the code.

   - ### With Docker

     First create the image:

     ```bash
     $ docker build -t slack-ray-translator .
     ```

     Then start a container using

     ```bash
     $ docker run -d --rm -p 3000:80 slack-ray-translator
     ```

     The `-p 3000:80` flag makes the app available on port 3000 on `localhost`, you can use any port you want. The `--rm` flag deletes the container when it is stopped, this is useful to prevent creating duplicate containers.

     **Note:** \
     The image has to be re-built after changes to the code because it copies the files when it is built. This means that the Docker method of running the app is less convenient during development.

     When running this app in Docker, the `DB_HOST` variable in `.env` is not `127.0.0.1`. This should be the hostname of the MySQL container, or just `host.docker.internal` (this is the address of Docker host).

   The app should now be running at `localhost` in the port you specified, e.g. http://localhost:3000.

4. To allow the Slack API to communicate with your app, the URL must be exposed to the public. You can do this with [boringproxy](https://boringproxy.io/), [ngrok](https://ngrok.com/) or whichever method you choose.

5. Create a new Slack workspace for the app (this is optional but is highly recommended because multiple identical apps in the same workspace will have conflicting commands).

   [Create a new app](https://api.slack.com/apps?new_app=1) and choose the "**From an app manifest**" option. Select the workspace you've just created and paste of contents of `manifest.yml` into the manifest section, but replace `<YOUR_DOMAIN>` with your public domain created in the previous step. This will set up the app to receive the correct events and assign the commands and shortcuts.

6. Install your Slack App to Slack Workspace with the URL `/slack/install`, e.g. `<your-domain>/slack/install`.

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
