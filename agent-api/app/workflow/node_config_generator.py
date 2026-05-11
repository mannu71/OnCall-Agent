"""
Node Config Form Generator: Auto-generate forms from skills and MCP schemas

Generates JSON Schema-based form configurations for workflow nodes based on:
1. Built-in node type schemas (agent, llm, tool, etc.)
2. MCP tool schemas (from connected MCP servers)
3. Skill definitions (from .agents/skills/)

The generated schemas can be used by frontend form libraries like:
- react-jsonschema-form
- Formik + Yup
- React Hook Form + Zod

Output Format:
-------------
{
    "schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "title": "Label"},
            "model": {"type": "string", "title": "Model", "enum": ["gpt-4", "claude-3"]}
        },
        "required": ["label", "model"]
    },
    "uiSchema": {
        "label": {"ui:autofocus": True},
        "model": {"ui:widget": "select"}
    },
    "formData": {
        "label": "My Agent",
        "model": "gpt-4"
    }
}
"""
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)


class NodeConfigGenerator:
    """Generates form configurations for workflow nodes."""
    
    # Built-in node type schemas
    BUILTIN_SCHEMAS = {
        "agent": {
            "schema": {
                "type": "object",
                "title": "Agent Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Agent Name",
                        "description": "Display name for this agent",
                        "default": "AI Agent"
                    },
                    "instructions": {
                        "type": "string",
                        "title": "Instructions",
                        "description": "System prompt / instructions for the agent",
                        "default": "You are a helpful AI assistant."
                    },
                    "description": {
                        "type": "string",
                        "title": "Description",
                        "description": "Optional description of what this agent does"
                    },
                    "temperature": {
                        "type": "number",
                        "title": "Temperature",
                        "description": "Sampling temperature (0.0 to 2.0)",
                        "minimum": 0.0,
                        "maximum": 2.0,
                        "default": 0.7
                    },
                    "maxTokens": {
                        "type": "integer",
                        "title": "Max Tokens",
                        "description": "Maximum tokens in response",
                        "minimum": 1,
                        "maximum": 100000,
                        "default": 4096
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "instructions": {"ui:widget": "textarea", "ui:options": {"rows": 5}},
                "description": {"ui:widget": "textarea", "ui:options": {"rows": 2}},
                "temperature": {"ui:widget": "range"},
                "maxTokens": {"ui:widget": "updown"}
            }
        },
        "llm": {
            "schema": {
                "type": "object",
                "title": "LLM Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "LLM Name",
                        "description": "Display name for this LLM",
                        "default": "GPT-4"
                    },
                    "model": {
                        "type": "string",
                        "title": "Model",
                        "description": "Model identifier",
                        "enum": [
                            "gpt-4",
                            "gpt-4-turbo",
                            "gpt-3.5-turbo",
                            "claude-3-opus",
                            "claude-3-sonnet",
                            "claude-3-haiku",
                            "anthropic.claude-3-5-sonnet-20241022-v2:0",
                            "anthropic.claude-3-5-haiku-20241022-v1:0"
                        ],
                        "default": "gpt-4"
                    },
                    "provider": {
                        "type": "string",
                        "title": "Provider",
                        "description": "LLM provider",
                        "enum": ["openai", "anthropic", "bedrock", "custom"],
                        "default": "openai"
                    },
                    "apiKeyEnvVar": {
                        "type": "string",
                        "title": "API Key Environment Variable",
                        "description": "Name of environment variable containing API key",
                        "default": "OPENAI_API_KEY"
                    },
                    "baseUrl": {
                        "type": "string",
                        "title": "Base URL",
                        "description": "Custom API base URL (optional)"
                    }
                },
                "required": ["label", "model"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "model": {"ui:widget": "select"},
                "provider": {"ui:widget": "select"},
                "apiKeyEnvVar": {"ui:help": "e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY"},
                "baseUrl": {"ui:placeholder": "https://api.openai.com/v1"}
            }
        },
        "tool": {
            "schema": {
                "type": "object",
                "title": "Tool Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Tool Name",
                        "description": "Display name for this tool",
                        "default": "Database Tool"
                    },
                    "mcpConfig": {
                        "type": "object",
                        "title": "MCP Configuration",
                        "properties": {
                            "command": {
                                "type": "string",
                                "title": "Command",
                                "description": "Command to run MCP server"
                            },
                            "args": {
                                "type": "array",
                                "title": "Arguments",
                                "description": "Command arguments",
                                "items": {"type": "string"}
                            },
                            "env": {
                                "type": "object",
                                "title": "Environment Variables",
                                "description": "Environment variables for MCP server",
                                "additionalProperties": {"type": "string"}
                            }
                        },
                        "required": ["command"]
                    }
                },
                "required": ["label", "mcpConfig"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "mcpConfig": {
                    "command": {"ui:placeholder": "uvx"},
                    "args": {
                        "ui:options": {"orderable": False},
                        "items": {"ui:placeholder": "mcp-server-sqlite"}
                    }
                }
            }
        },
        "orchestrator": {
            "schema": {
                "type": "object",
                "title": "Orchestrator Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Orchestrator Name",
                        "default": "SQL Orchestrator"
                    },
                    "sqlFile": {
                        "type": "string",
                        "title": "SQL File",
                        "description": "Path to SQL workflow file"
                    },
                    "sqlContent": {
                        "type": "string",
                        "title": "SQL Content",
                        "description": "Inline SQL workflow content"
                    },
                    "description": {
                        "type": "string",
                        "title": "Description"
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "sqlFile": {"ui:placeholder": "workflow.sql"},
                "sqlContent": {"ui:widget": "textarea", "ui:options": {"rows": 10}},
                "description": {"ui:widget": "textarea"}
            }
        },
        "cloudwatchAnalyzer": {
            "schema": {
                "type": "object",
                "title": "CloudWatch Analyzer Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Analyzer Name",
                        "default": "CloudWatch Analyzer"
                    },
                    "logGroups": {
                        "type": "array",
                        "title": "Log Groups",
                        "description": "CloudWatch log groups to analyze",
                        "items": {"type": "string"},
                        "minItems": 1
                    },
                    "analysisType": {
                        "type": "string",
                        "title": "Analysis Type",
                        "enum": [
                            "error-patterns",
                            "activity-summary",
                            "anomaly-detection",
                            "correlation"
                        ],
                        "default": "error-patterns"
                    },
                    "timeRange": {
                        "type": "string",
                        "title": "Time Range",
                        "description": "Time range to analyze (e.g., 15m, 1h, 7d)",
                        "default": "1h"
                    },
                    "awsRegion": {
                        "type": "string",
                        "title": "AWS Region",
                        "default": "us-east-1"
                    },
                    "awsProfile": {
                        "type": "string",
                        "title": "AWS Profile",
                        "description": "AWS profile name (optional)"
                    },
                    "errorThreshold": {
                        "type": "integer",
                        "title": "Error Threshold",
                        "description": "Alert threshold for error count",
                        "minimum": 1,
                        "default": 10
                    },
                    "enableAlerts": {
                        "type": "boolean",
                        "title": "Enable Alerts",
                        "default": False
                    }
                },
                "required": ["label", "logGroups", "analysisType"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "logGroups": {
                    "ui:options": {"orderable": False},
                    "items": {"ui:placeholder": "/aws/lambda/my-function"}
                },
                "analysisType": {"ui:widget": "select"},
                "timeRange": {"ui:placeholder": "1h"},
                "awsRegion": {"ui:placeholder": "us-east-1"},
                "errorThreshold": {"ui:widget": "updown"},
                "enableAlerts": {"ui:widget": "checkbox"}
            }
        },
        "scheduler": {
            "schema": {
                "type": "object",
                "title": "Scheduler Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Scheduler Name",
                        "default": "Workflow Trigger"
                    },
                    "description": {
                        "type": "string",
                        "title": "Description"
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "description": {"ui:widget": "textarea"}
            }
        },
        "chat": {
            "schema": {
                "type": "object",
                "title": "Chat Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Chat Name",
                        "default": "Chat Interface"
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True}
            }
        },
        "output": {
            "schema": {
                "type": "object",
                "title": "Output Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Output Name",
                        "default": "Output"
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True}
            }
        },
        "memory": {
            "schema": {
                "type": "object",
                "title": "Memory Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Memory Name",
                        "default": "Memory Store"
                    },
                    "type": {
                        "type": "string",
                        "title": "Memory Type",
                        "enum": ["short-term", "long-term", "episodic"],
                        "default": "short-term"
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "type": {"ui:widget": "select"}
            }
        }
    }
    
    def __init__(self):
        """Initialize node config generator."""
        self._skill_schemas_cache: Optional[Dict[str, Any]] = None
    
    def get_node_schema(
        self,
        node_type: str,
        current_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get form schema for a node type.
        
        Args:
            node_type: Node type (agent, llm, tool, etc.)
            current_data: Current node data (for formData)
            
        Returns:
            Form configuration with schema, uiSchema, and formData
        """
        if node_type not in self.BUILTIN_SCHEMAS:
            return self._get_unknown_node_schema(node_type, current_data)
        
        config = self.BUILTIN_SCHEMAS[node_type].copy()
        
        # Add current data as formData
        if current_data:
            config["formData"] = current_data
        
        return config
    
    def get_all_node_types(self) -> List[Dict[str, Any]]:
        """Get list of all available node types with metadata.
        
        Returns:
            List of node type metadata dicts
        """
        node_types = []
        
        for node_type, config in self.BUILTIN_SCHEMAS.items():
            schema = config["schema"]
            node_types.append({
                "type": node_type,
                "title": schema.get("title", node_type.title()),
                "description": schema.get("description", f"{node_type.title()} node"),
                "category": self._get_node_category(node_type)
            })
        
        return node_types
    
    def get_mcp_tool_schema(
        self,
        server_id: str,
        tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get form schema for an MCP tool.
        
        Args:
            server_id: MCP server ID
            tool_name: Tool name
            
        Returns:
            Form configuration or None if tool not found
        """
        # This would integrate with MCPClientManager to get tool schemas
        # For now, return a placeholder
        return {
            "schema": {
                "type": "object",
                "title": f"{tool_name} Configuration",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "title": "Server ID",
                        "default": server_id,
                        "readOnly": True
                    },
                    "tool_name": {
                        "type": "string",
                        "title": "Tool Name",
                        "default": tool_name,
                        "readOnly": True
                    }
                }
            },
            "uiSchema": {
                "server_id": {"ui:readonly": True},
                "tool_name": {"ui:readonly": True}
            }
        }
    
    def get_skill_schemas(self) -> Dict[str, Any]:
        """Get form schemas for all available skills.
        
        Returns:
            Dict mapping skill names to form configurations
        """
        if self._skill_schemas_cache is not None:
            return self._skill_schemas_cache
        
        skills = {}
        skills_dir = Path(".agents/skills")
        
        if not skills_dir.exists():
            return skills
        
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            
            try:
                # Parse SKILL.md for schema information
                # This is a simplified version - real implementation would parse frontmatter
                skill_name = skill_dir.name
                skills[skill_name] = {
                    "schema": {
                        "type": "object",
                        "title": f"{skill_name.title()} Skill",
                        "properties": {
                            "enabled": {
                                "type": "boolean",
                                "title": "Enabled",
                                "default": True
                            }
                        }
                    },
                    "uiSchema": {
                        "enabled": {"ui:widget": "checkbox"}
                    }
                }
            except Exception as e:
                logger.warning(f"Failed to load skill schema for {skill_dir.name}: {e}")
        
        self._skill_schemas_cache = skills
        return skills
    
    def _get_unknown_node_schema(
        self,
        node_type: str,
        current_data: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Get schema for unknown node type.
        
        Args:
            node_type: Unknown node type
            current_data: Current node data
            
        Returns:
            Generic form configuration
        """
        return {
            "schema": {
                "type": "object",
                "title": f"{node_type.title()} Configuration",
                "properties": {
                    "label": {
                        "type": "string",
                        "title": "Label",
                        "default": node_type.title()
                    },
                    "description": {
                        "type": "string",
                        "title": "Description"
                    }
                },
                "required": ["label"]
            },
            "uiSchema": {
                "label": {"ui:autofocus": True},
                "description": {"ui:widget": "textarea"}
            },
            "formData": current_data or {}
        }
    
    def _get_node_category(self, node_type: str) -> str:
        """Get category for a node type.
        
        Args:
            node_type: Node type
            
        Returns:
            Category string
        """
        categories = {
            "agent": "AI",
            "llm": "AI",
            "tool": "Integration",
            "orchestrator": "Data",
            "cloudwatchAnalyzer": "Monitoring",
            "scheduler": "Control",
            "chat": "Interface",
            "output": "Interface",
            "memory": "Storage"
        }
        return categories.get(node_type, "Other")


# Global generator instance
node_config_generator = NodeConfigGenerator()
