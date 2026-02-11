# Feature Specification: Notification API Admin Dashboard  
Target Frameworks: Python 3.13, NiceGUI, SQLite, SQLAlchemy, Pydantic 
Target API: [VA Notification API](https://github.com/department-of-veterans-affairs/notification-api)

## 1. System Architecture & Tech Stack  
* Frontend/UI: NiceGUI  
   * Layout: Standard "App Shell" layout (Left Sidebar for Navigation, Top Status Bar, Main Content Area).  
   * State Management: Native NiceGUI state (bindings) + SQLite data.  
* Local Database: SQLite (via SQLAlchemy ORM).  
   * Purpose: Local caching of remote resources (Services, Templates) to enable fast client-side filtering and persistence of user configurations.  
* Security & Encryption:  
   * Library: cryptography (Fernet).  
   * Scope: API Key Secrets and Basic Auth credentials stored in the local SQLite DB must be encrypted at rest.  
   * Key Management: The application should use a Master Password (or environment variable) to derive the encryption key for the local database.  
* Concurrency:  
   * Use Python asyncio for all network requests.  
   * Use nicegui.run.io_bound for heavy crypto or parsing tasks to avoid blocking the UI event loop.  

## 2. Authentication & Configuration  
The tool must handle two distinct authentication layers:  

### A. Global Admin Auth (Basic Auth)  
* Usage: Used for fetching system-wide administrative data (e.g., listing all Services or Templates).  
* Routes: GET /service, etc.  
* Storage: Credentials (username/password) stored in a settings table, encrypted.  

### B. Service-Level Auth (Bearer Token / JWT)  
* Usage: Used for transactional operations, specifically sending notifications.  
* Routes: POST /v2/notifications/email, POST /v2/notifications/sms.  
* Mechanism: JSON Web Token (JWT) signed with a Service's API Secret. It is unencrypted when used.
* Storage: when API keys are generated via the POST API key route, the value in the data object returned is stored encrypted to the local_api_keys table.  

## 3. Database Schema (Local Cache)  
The schema should mirror the API's structure for caching, plus local-only tables for configuration.  
Core Tables  
* services  
   * id (String/UUID, Primary Key)  
   * name (String, Indexed for search)  
   * active (Boolean)  
   * restricted (Boolean)  
   * limit (Integer)  
   * created_at (DateTime)  
   * updated_at (DateTime)  
* templates  
   * id (String/UUID, Primary Key)  
   * service_id (String/UUID)  
   * name (String)  
   * template_type (Enum: 'email', 'sms')  
   * content (Text) - Stores the body content for parsing variables.  
   * subject (String) - Email only.  
   * version (Integer)  
* api_keys
   * id (String/UUID, Primary Key)
   * name (String)
   * expiry_date (datetime)
   * created_by (String/UUID)
* local_api_keys (Local Only)  
   * id (Integer, Primary Key, Auto-increment)  
   * service_id (String/UUID)  
   * key_name (String) - Friendly name for the key.  
   * key_secret (Bytes/String) - Encrypted storage of the API secret.  
   * key_type (Enum: 'normal', 'team', 'test')  

## 4. UI/UX Feature Breakdown  
### A. Global Layout  
* Sidebar: Navigation links:  
   * Dashboard (Home)  
   * Send Notification (The Form)  
   * Services (Data Grid)  
   * Templates (Data Grid)  
   * Settings (Keys & Auth)  
* Status Bar:  
   * Indicator: "API Status: [Online/Offline]"  
   * Global Action: "Refresh All Data" button.  

### B. "Send Notification" Page (Primary Feature)  
This page acts as a visual client/form for the API.  
* Environment Selection:  
   * Dropdown: Development, Staging, Production.  
   * Effect: Changes the base_url used for the request.  
* Service Selection:  
   * Dropdown: Lists all cached services. Searchable by name.  
   * Effect: Filters the options for API Keys and Templates.  
* Authentication Source:  
   * Dropdown: Lists local_api_keys associated with the selected service_id.  
* Template Configuration:  
   * Toggle: Email vs SMS.  
   * Dropdown: Lists cached templates for the selected Service + Type.  
* Dynamic Personalization:  
   * Logic: When a template is selected, the application parses the content (and subject) for placeholders using the syntax ((variable)).  
   * UI Generation: Automatically renders a form input for each unique variable found (e.g., ((first_name)) -> Text Input "First Name").  
* Recipient:  
   * Input: Email Address or Phone Number (validated based on template type).  
* Execute:  
   * Action: "Send Notification".  
   * Feedback: Show a loading spinner, then display the full JSON response in a ui.log or code block component.  

### C. Resource Pages (Services, Templates)  
Each resource page follows a consistent "Datagrid" pattern:  
* Data Table:  
   * Use ui.table with sortable columns.  
   * Client-Side Filtering: Text input to filter rows by Name, Email, or ID immediately (no API call required).  
* Sync Logic:  
   * "Sync [Resource]" button on top right.  
   * Async fetch updates the SQLite cache, then refreshes the UI table.  
   * For Templates, the sync must be "Service Aware" (fetch templates for a specific service, or loop through all services if doing a full sync).  

### D. Settings & Key Management  
* API Configuration: Form to set the Notification API Base URL. Save it in the database, or pull from configuration file. Needs a base URL per-environment.
* Global Auth: Input for Basic Auth Username/Password (saved encrypted). Needs to be saved per-environment.
* Key Manager:  
   * Form to add new API Keys.  
   * Fields: Service (Dropdown), Key Name, Secret Key.  
   * Action: Encrypt and save to local_api_keys.  

## 5. Functional Logic & Requirements  
### 1. The Sync Engine  
* Batching: Avoid 100+ concurrent requests. When syncing all templates, use a semaphore or queue to limit concurrent API calls (e.g., 25 at a time).  
* Background Tasks: Long-running syncs (like fetching templates for 50 services) must run in the background. The UI should show a progress bar or notification (e.g., "Syncing Service 5/50...").  

### 2. Notification Sender Logic  
* Token Generation:  
   * Implement the specific JWT signing algorithm required by the VA Notification API using PyJWT.  
   * Payload claims: iss (Service ID), iat (Current Time).  
   * Headers: typ (JWT), alg (HS256).  
* Payload Construction:  
 ```
 {  
  "template_id": "uuid",  
  "email_address": "user@example.com",  
  "personalisation": {  
    "dynamic_field_1": "value",  
    "dynamic_field_2": "value"  
  }  
}
```

### 3. Error Handling  
* Validation: Ensure personalization fields are not empty before sending.  
* Validation: Ensure the API key being used is not expired before sending, check "expiry_date" field
* API Errors: Gracefully handle 400 Bad Request (Validation), 403 Unauthorized (Bad Key), and 429 Too Many Requests.  
* Feedback: Use ui.notify() for transient errors and modal dialogs for critical setup errors (e.g., "No API Key configured").

## 6. Running Environment
This application should run in a docker container and be accessible via localhost on a local browser. The SQLite database should persist upon restarting the container(s). A docker compose file will allow for easy setup and running of the application.
