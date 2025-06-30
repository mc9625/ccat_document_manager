# Document Manager Plugin for Cheshire Cat AI By NuvolaProject

> **Production-ready document management with hardened authentication**

[![Version](https://img.shields.io/badge/version-2.0.3-blue.svg)](https://github.com/cheshire-cat-ai/document-manager-plugin)
[![Python](https://img.shields.io/badge/python-3.8+-green.svg)](https://python.org)
[![Cheshire Cat](https://img.shields.io/badge/cheshire--cat-1.4+-purple.svg)](https://cheshirecat.ai)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)

A comprehensive document management plugin for Cheshire Cat AI that provides both web interface and CLI tools to manage documents in the Rabbit Hole with enterprise-grade security.

## ✨ Features

### 🔐 Security & Authentication
- **Hardened JWT Authentication** - Admin-only access with PLUGINS/EDIT permission required
- **FastAPI Dependency Injection** - Secure endpoint protection
- **Multi-level Admin Access Control** - Granular permission checking

### 🌐 Web Interface
- **Modern Responsive Design** - Works on desktop and mobile
- **Real-time Document Management** - Upload, view, search, and delete documents
- **Advanced Search & Filtering** - Search by filename, content, or metadata
- **Document Statistics** - Comprehensive memory usage analytics
- **Theme Synchronization** - Automatically syncs with Cat's dark/light theme

### 🛠️ Command Line Tools
- **Interactive CLI Commands** - User-friendly document operations
- **Smart Prompt Switching** - Automatic English prompts for consistent responses
- **Comprehensive Statistics** - Detailed memory and document analytics
- **Batch Operations** - Efficient document management

### ⚡ Performance & Reliability
- **Optimized Memory Operations** - Multiple backend fallbacks for stability
- **Robust Error Handling** - Graceful failure recovery
- **Comprehensive Logging** - Detailed operation tracking
- **Multi-format Support** - PDF, TXT, DOCX, and more

## 🚀 Quick Start

### Installation

1. **Download the plugin** as a ZIP file or clone the repository
2. **Install via Cheshire Cat Admin Panel**:
   - Navigate to the Plugins section
   - Upload the ZIP file or use the Plugin Registry
3. **Activate the plugin** and restart your Cat instance

### Requirements

- Cheshire Cat AI >= v1.4.0
- Admin privileges (PLUGINS/EDIT permission)
- Modern web browser for the web interface

## 📖 Usage

### Web Interface

Access the web interface at: `http://your-cat-instance/custom/documents`

**Features:**
- 📁 **View all documents** with preview and metadata
- 🔍 **Search and filter** documents by name or content
- 📊 **Document statistics** and memory usage analytics
- 🗑️ **Delete documents** with confirmation dialogs
- 📤 **Upload new documents** with drag-and-drop support

### CLI Commands

#### Basic Operations

```bash
# List all documents
list_documents

# Search for specific documents
list_documents user manual
list_documents report 2024

# Remove a specific document
remove_document filename.pdf

# Get document statistics
document_statistics basic
document_statistics detailed
```

#### Advanced Operations

```bash
# Clear all documents (requires confirmation)
clear_all_documents CONFIRM

# Test plugin functionality
test_document_plugin "test message"
```

#### Quick Commands

The plugin also responds to natural language commands:

```bash
# These work automatically
"list documents"
"show documents" 
"document list"
"rabbit hole status"
"memory status"
"documents"
```

## 🔧 Configuration

### Settings

The plugin supports the following configuration options:

```python
class DocumentManagerSettings:
    max_documents_per_page: int = 25        # Documents per page (5-100)
    show_document_preview: bool = True      # Show document preview
    preview_length: int = 200               # Preview length in characters
    admin_user_ids: str = "admin"          # Comma-separated admin user IDs
    enable_search_optimization: bool = True # Optimize search performance
    memory_chunk_limit: int = 1000         # Memory chunk processing limit
```

### Authentication

The plugin enforces strict admin-only access:

- **JWT Token Required** - Must contain PLUGINS/EDIT permission
- **Admin User Verification** - User must be recognized as admin
- **Automatic Fallback** - Graceful degradation for non-admin users

## 🏗️ Architecture

### Core Components

```
document_manager/
├── ccat_document_manager.py    # Main plugin file
├── document_manager.html       # Web interface
├── document_manager.css        # Styling
├── document_manager.js         # Frontend logic
└── README.md                   # Documentation
```

### Security Architecture

1. **Authentication Layer** - JWT validation and admin permission checking
2. **API Endpoints** - Secured FastAPI endpoints with dependency injection
3. **Memory Operations** - Safe document operations with error handling
4. **Frontend Security** - CSRF protection and input validation

### API Endpoints

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/custom/documents` | GET | Web interface | ✅ Admin |
| `/custom/documents/api/documents` | GET | List documents | ✅ Admin |
| `/custom/documents/api/stats` | GET | Document statistics | ✅ Admin |
| `/custom/documents/api/remove` | POST | Remove document | ✅ Admin |
| `/custom/documents/api/clear` | POST | Clear all documents | ✅ Admin |

## 🛡️ Security Features

### Authentication Mechanisms

1. **JWT Header Authentication**
   ```
   Authorization: Bearer <jwt-token>
   ```

2. **Cookie Authentication**
   ```
   ccat_user_token=<jwt-token>
   ```

3. **Query Parameter Authentication**
   ```
   ?token=<jwt-token>
   ```

### Permission Validation

The plugin validates that users have:
- Valid JWT token
- PLUGINS/EDIT permission in the token payload
- Recognition as an admin user by the Cat system

### Security Best Practices

- **No data exposure** to unauthorized users
- **Input validation** on all endpoints
- **Error message sanitization** to prevent information leakage
- **Audit logging** for all document operations

## 🔍 Troubleshooting

### Common Issues

#### "Access denied" errors
- **Cause**: Insufficient permissions
- **Solution**: Ensure user has PLUGINS/EDIT permission and admin status

#### Documents not loading
- **Cause**: Memory system compatibility issues
- **Solution**: Check Cat logs for memory system errors; plugin includes fallback mechanisms

#### Web interface not accessible
- **Cause**: Authentication or routing issues  
- **Solution**: Verify JWT token and check `/custom/documents` endpoint availability

### Debug Mode

Enable debug logging in your Cat configuration:

```python
# In your Cat configuration
log_level = "DEBUG"
```

The plugin will provide detailed logging for troubleshooting.

## 📊 Performance Considerations

### Memory Optimization

- **Chunked Processing** - Handles large document sets efficiently
- **Lazy Loading** - Documents loaded on demand
- **Caching Mechanisms** - Reduced memory system calls

### Scalability

- **Pagination Support** - Configurable documents per page
- **Search Optimization** - Indexed search with fallbacks
- **Batch Operations** - Efficient bulk document handling

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

1. **Fork the repository** and create a feature branch
2. **Follow the code style** established in the project
3. **Add tests** for new functionality
4. **Update documentation** as needed
5. **Submit a pull request** with a clear description

### Development Setup

```bash
# Clone the repository
git clone https://github.com/mc9625/ccat_document_manager.git

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
python -m pytest tests/
```

## 📝 Changelog

### v2.0.3 - AUTH GATE FIX
- 🔒 Hardened JWT authentication with brutal auth check
- 🔧 Fixed endpoint dependencies for proper admin verification
- ✨ Enhanced error handling and user feedback
- 🎨 Improved web interface responsiveness

### v2.0.2 - Security Update
- 🔐 Enhanced permission checking mechanisms
- 🛡️ Added FastAPI dependency injection for security
- 📊 Improved statistics and analytics

### v2.0.0 - Production Ready
- 🚀 Complete rewrite for production environments
- 🌐 Modern web interface with responsive design
- 🛠️ Comprehensive CLI tools
- ⚡ Optimized memory operations with fallbacks

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Cheshire Cat AI Community** - For the amazing framework and support
- **Contributors** - For their valuable contributions and feedback
- **Beta Testers** - For helping identify and fix issues

## 📞 Support

- **Documentation**: [Cheshire Cat AI Docs](https://cheshirecat.ai/docs/)
- **Community**: [Discord Server](https://discord.gg/cheshire-cat-ai)
- **Issues**: [GitHub Issues](https://github.com/mc9625/ccat_document_manager/issues)
- **Discussions**: [GitHub Discussions](https://github.com/mc9625/ccat_document_manager/discussions)

---

**Made with ❤️ for the Cheshire Cat AI Community by NuvolaProject**