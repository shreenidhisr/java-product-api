# java-product-api

A simple Spring Boot REST API for demonstrating **AI-powered automated test generation** via GitHub Actions.

When a pull request is opened, a GitHub Actions workflow fetches the PR diff, sends it to Claude (Anthropic), and automatically opens a PR in the companion test repo with newly generated pytest tests.

---

## API Endpoints

| Method | Path             | Description          |
|--------|------------------|----------------------|
| GET    | /products        | List all products    |
| GET    | /products/{id}   | Get product by ID    |
| POST   | /products        | Create a product     |
| PUT    | /products/{id}   | Update a product     |
| DELETE | /products/{id}   | Delete a product     |

### Product schema

```json
{
  "id":    1,
  "name":  "Laptop",
  "price": 999.99
}
```

---

## Running Locally

```bash
./mvnw spring-boot:run
```

The API starts on `http://localhost:8080`.

---

## AI Test Generation Setup

### What it does

Every time you open or update a PR, the workflow in `.github/workflows/ai-test-gen.yml`:

1. Fetches the PR diff
2. Reads existing tests from the test repo as style examples
3. Calls **Claude** (`claude-3-5-sonnet`) with the diff + examples
4. Commits the generated pytest file to a new branch in the test repo
5. Opens a PR in the test repo for review

### Step 1 — Create both GitHub repos

| Repo | Purpose |
|------|---------|
| `your-org/java-product-api` | This repo (Java app) |
| `your-org/pytest-product-tests` | Companion test repo |

Push each local folder to its respective repo:

```bash
# Java app repo
cd java-product-api
git init && git add . && git commit -m "initial commit"
gh repo create your-org/java-product-api --private --source=. --push

# Test repo
cd ../pytest-product-tests
git init && git add . && git commit -m "initial commit"
gh repo create your-org/pytest-product-tests --private --source=. --push
```

### Step 2 — Create a GitHub Personal Access Token (PAT)

The workflow needs write access to the test repo to push branches and open PRs.

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Click **Generate new token**
3. Set **Resource owner** to your org/account
4. Under **Repository access**, select `pytest-product-tests` only
5. Grant these permissions:
   - **Contents**: Read and write
   - **Pull requests**: Read and write
6. Copy the token

### Step 3 — Add GitHub Actions secrets to `java-product-api`

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name      | Value                                                        |
|------------------|--------------------------------------------------------------|
| `GEMINI_API_KEY` | Your Gemini API key (free — see below)                       |
| `TEST_REPO_TOKEN`| The PAT you created in Step 2                                |
| `TEST_REPO`      | `your-org/pytest-product-tests`                              |

### Step 4 — Test it end-to-end

Create a PR that adds a new field to `Product.java`, e.g.:

```java
// Add to Product.java
private String description;
// + getter, setter, constructor update
```

Open a PR — within ~60 seconds you should see:
- The `AI Test Generation` workflow running on the PR
- A new PR opened in `pytest-product-tests` with auto-generated tests like `test_description_field.py`

---

## Project Structure

```
java-product-api/
├── .github/
│   ├── workflows/
│   │   └── ai-test-gen.yml          # Main AI test generation workflow
│   └── scripts/
│       └── generate_tests.py        # Python script: calls Claude, writes test file
├── src/main/java/com/demo/
│   ├── Application.java
│   ├── Product.java                 # Model — add new fields here to trigger AI tests
│   ├── ProductService.java          # In-memory store
│   └── ProductController.java       # REST endpoints
├── src/main/resources/
│   └── application.properties
└── pom.xml
```

---

## Getting a Free Gemini API Key

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API key** — no credit card required
4. Copy the key (starts with `AIza...`)

Free tier limits: **15 requests/min**, **1 million tokens/day** — more than enough for a team doing dozens of PRs per day.

## How the Gemini Prompt Works

The script in `.github/scripts/generate_tests.py` sends Gemini 1.5 Flash:

- **System prompt**: Instructions to act as a QA engineer, follow the existing test style, use the right fixtures, cover new fields/endpoints thoroughly, and skip infrastructure-only changes
- **User message**: The PR diff + top-5 most semantically similar test functions (retrieved via embedding index — not all tests)

Gemini responds with a JSON object containing the filename and full test code. The workflow then commits that file to the test repo.

---

## Companion Repo

See `pytest-product-tests/` for the test project structure, fixtures, and example tests.
