# Straker Translate for Slack

The [Slack App](https://api.slack.com/) for Straker LanguageCloud. This app allows clients to view and manage their translation jobs from Slack.

## Development

### Requirements

- [Python 3.11](https://www.python.org/)
- [Pipenv](https://pipenv.pypa.io/)
- MySQL
- MongoDB (docker compose)
- [pt-languagecloud-api](https://bitbucket.org/strakertech/pt-languagecloud-api)
- Redis (in docker-compose)
- [redis-stream-proxy](https://bitbucket.org/strakertech/redis-stream-proxy-api/) Called by other apps to send events to slack
- [redis-slack-consumer](https://bitbucket.org/strakertech/redis-slack-consumer) proccesses events
- [wb-task-consumer](https://bitbucket.org/strakertech/wb-task-consumer/) used for transcribing of video posted to slack
- [verify-task-consumer](https://bitbucket.org/strakertech/pt-verify-consumer/) used for document machine translatin
### Setup

1. Set up environment variables by copying the `.env.example` to `.env` and writing the configuration for your app.

   ```bash
   $ cp .env.example .env
   ```

   The `.env` file contains sensitive and device-specific information so is not committed to the repository.

2. Download the credentials of your IBM Watson Assistant service instance and place the file (named `ibm-credentials.env`) in the root directory.

   You can find the service instance in your [resource list](https://cloud.ibm.com/resources) under **Services and software**, the product should be **Watson Assistant**. After opening the service instance page, click the _Download_ button in the _Credentials_ section.

3. There are 2 ways to run the app: normally with Pipenv or with Docker.

   - ### Without Docker

     Create a Pipenv virtual environment and install the dependencies using

     ```bash
     $ pipenv install --dev
     ```

     Then start the app using

     ```bash
     $ pipenv run uvicorn app.main:app --reload --port 3000
     ```

     The `--reload` flag updates the API automatically when there are changes to the code.

   - ### With Docker

     You should be able to just run

     ```bash
     $ docker compose up -d
     ```

     Note: Your .env db host will be local-percona

   The app should now be running at `localhost` in the port you specified, e.g. http://localhost:3000.

4. To allow the Slack API to communicate with your app, the URL must be exposed to the public. You can do this with [boringproxy](https://boringproxy.io/), [ngrok](https://ngrok.com/) or whichever method you choose.

5. Create a new Slack workspace for the app (this is optional but is highly recommended because multiple identical apps in the same workspace will have conflicting commands).

   [Create a new app](https://api.slack.com/apps?new_app=1) and choose the "**From an app manifest**" option. Select the workspace you've just created and paste of contents of `manifest.yml` into the manifest section, but replace `<YOUR_DOMAIN>` with your public domain created in the previous step. This will set up the app to receive the correct events and assign the commands and shortcuts. Update .env file Slack API fields with the app credentials

6. Install your Slack App to Slack Workspace with the URL `/slack/install`, e.g. `<your-domain>/slack/install`.

   You can share this URL or embed it in a link on a web page for anyone to install (must have public distribution enabled, Settings -> Manage Distribution). **Make sure the app is secure before making it available to the public.**

### Dependencies

These are Dependencies you will probably need to set up. Check the repo for readme for setup.

local-redis - this is in the docker repo development/servers/redis/docker-compose.yml

redis-slack-consumer - This is for events. You can find the repo [here](https://bitbucket.org/strakertech/redis-slack-consumer/)

slack-sdk - If you need to update the sdk you can find repo [here](https://bitbucket.org/strakertech/ray-python-sdk/)

languagecloud-api - On going work to port to using languagecloud-api repo [here](https://bitbucket.org/strakertech/pt-languagecloud-api/)

### Troubleshooting

#### **ModuleNotFoundError: No module named '...'**

This means that some Python modules (dependencies) are not installed. To fix this, install the dependencies by running

```bash
$ pipenv install --dev
```

This will create a virtual enviroment and install the dependencies from `Pipfile`.

#### **[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate**

If you are using macOS and installed Python directly from https://www.python.org, you may get an error like this when installing the app to Slack:

```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:997)
```

To fix this, install the SSL certificates by running the script at `/Applications/Python\ 3.11/Install\ Certificates.command` (or just double clicking the `Install Certificates.command` file in the `Applications/Python 3.11` directory), then restart the app.

### Automated Tests

All tests have been written using the [PyTest](https://docs.pytest.org/en/latest/) package. Tests are kept in the `tests` folder and can be run with:

```bash
$ pipenv run pytest
```

You can use the [coverage](https://coverage.readthedocs.io/) package to measure the code coverage of the tests. Scan the code and generate a report with:

```bash
$ pipenv run coverage run --source=app -m pytest
> ...
$ pipenv run coverage report -m  # or
$ pipenv run coverage html       # HTML in htmlcov/
```

### Linting and Formatting Code

This package uses [Ruff](https://github.com/astral-sh/ruff) for linting and [Black](https://black.readthedocs.io/en/stable/) for formatting.

### Static Type Checking

We use [mypy](http://mypy-lang.org) to perform static type checks on the codebase and can be run from the command line:

```bash
$ pipenv run python -m mypy app/**/*.py
```

### Slack API fields in env file

These can be found once created in step 5 on the app page at https://api.slack.com/apps/

### Translation

translate.py has methods to use sitemanger.obj_stringtranslator db for translation
You can use the \_() function to translate your strings
The locale is read from client info
