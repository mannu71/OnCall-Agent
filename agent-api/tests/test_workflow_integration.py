"""
Integration tests for workflow streaming architecture.

Tests the complete flow:
1. Workflow translation (React Flow → Engine)
2. Event normalization (Internal → Canonical)
3. SSE streaming
4. Node config generation
"""
import pytest
from datetime import datetime, timezone


class TestEventAdapter:
    """Test event adapter normalization."""
    
    def test_normalize_node_start(self):
        """Test node_started → node_start normalization."""
        from app.workflow.event_adapter import event_adapter
        
        internal_event = {
            "event_type": "node_started",
            "timestamp": "2024-01-01T00:00:00Z",
            "data": {
                "node_id": "agent-1",
                "node_type": "agent",
                "label": "AI Agent"
            }
        }
        
        canonical_event = event_adapter.normalize(internal_event)
        
        assert canonical_event.event_type == "node_start"
        assert canonical_event.data["nodeId"] == "agent-1"
        assert canonical_event.data["nodeType"] == "agent"
        assert canonical_event.data["label"] == "AI Agent"
        assert canonical_event.timestamp == "2024-01-01T00:00:00Z"
    
    def test_normalize_node_done(self):
        """Test node_completed → node_done normalization."""
        from app.workflow.event_adapter import event_adapter
        
        internal_event = {
            "event_type": "node_completed",
            "data": {
                "node_id": "agent-1",
                "node_type": "agent",
                "status": "success",
                "duration": 12.5,
                "output": "Task completed"
            }
        }
        
        canonical_event = event_adapter.normalize(internal_event)
        
        assert canonical_event.event_type == "node_done"
        assert canonical_event.data["nodeId"] == "agent-1"
        assert canonical_event.data["status"] == "success"
        assert canonical_event.data["duration"] == 12.5
        assert canonical_event.data["output"] == "Task completed"
    
    def test_normalize_agent_token(self):
        """Test llm_token → agent_token normalization."""
        from app.workflow.event_adapter import event_adapter
        
        internal_event = {
            "event_type": "llm_token",
            "data": {
                "token": "Hello",
                "node_id": "agent-1"
            }
        }
        
        canonical_event = event_adapter.normalize(internal_event)
        
        assert canonical_event.event_type == "agent_token"
        assert canonical_event.data["token"] == "Hello"
        assert canonical_event.data["node_id"] == "agent-1"
    
    def test_passthrough_workflow_events(self):
        """Test workflow events pass through unchanged."""
        from app.workflow.event_adapter import event_adapter
        
        internal_event = {
            "event_type": "workflow_started",
            "data": {
                "execution_id": "exec-123",
                "workflow_name": "Test Workflow"
            }
        }
        
        canonical_event = event_adapter.normalize(internal_event)
        
        assert canonical_event.event_type == "workflow_started"
        assert canonical_event.data["execution_id"] == "exec-123"
        assert canonical_event.data["workflow_name"] == "Test Workflow"
    
    def test_to_sse_format(self):
        """Test SSE format generation."""
        from app.workflow.event_schema import NodeStartEvent
        
        event = NodeStartEvent(
            data={
                "nodeId": "agent-1",
                "nodeType": "agent",
                "label": "AI Agent"
            }
        )
        
        sse_string = event.to_sse()
        
        assert "data: " in sse_string
        assert "node_start" in sse_string
        assert "nodeId" in sse_string
        assert sse_string.endswith("\n\n")


class TestWorkflowTranslator:
    """Test workflow translator."""
    
    def test_translate_valid_workflow(self):
        """Test translating a valid workflow."""
        from app.workflow.workflow_translator import workflow_translator
        
        workflow = {
            "name": "Test Workflow",
            "nodes": [
                {
                    "id": "agent-1",
                    "type": "agent",
                    "position": {"x": 100, "y": 200},
                    "data": {"label": "AI Agent"}
                }
            ],
            "edges": []
        }
        
        engine_workflow = workflow_translator.translate(workflow)
        
        assert engine_workflow["name"] == "Test Workflow"
        assert len(engine_workflow["nodes"]) == 1
        assert engine_workflow["nodes"][0]["id"] == "agent-1"
        assert "metadata" in engine_workflow
        assert "created_at" in engine_workflow["metadata"]
    
    def test_validate_missing_required_field(self):
        """Test validation catches missing required fields."""
        from app.workflow.workflow_translator import (
            workflow_translator,
            NodeValidationError
        )
        
        workflow = {
            "name": "Test Workflow",
            "nodes": [
                {
                    "id": "llm-1",
                    "type": "llm",
                    "position": {"x": 0, "y": 0},
                    "data": {}  # Missing required 'label' and 'model'
                }
            ],
            "edges": []
        }
        
        with pytest.raises(NodeValidationError) as exc_info:
            workflow_translator.translate(workflow)
        
        assert "missing required field" in str(exc_info.value).lower()
    
    def test_validate_invalid_connection(self):
        """Test validation catches invalid connections."""
        from app.workflow.workflow_translator import (
            workflow_translator,
            EdgeValidationError
        )
        
        workflow = {
            "name": "Test Workflow",
            "nodes": [
                {
                    "id": "agent-1",
                    "type": "agent",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Agent"}
                },
                {
                    "id": "llm-1",
                    "type": "llm",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "LLM", "model": "gpt-4"}
                }
            ],
            "edges": [
                {
                    "id": "edge-1",
                    "source": "agent-1",  # Invalid: agent cannot connect to llm
                    "target": "llm-1"
                }
            ]
        }
        
        with pytest.raises(EdgeValidationError) as exc_info:
            workflow_translator.translate(workflow)
        
        assert "cannot" in str(exc_info.value).lower()
    
    def test_validate_duplicate_node_ids(self):
        """Test validation catches duplicate node IDs."""
        from app.workflow.workflow_translator import (
            workflow_translator,
            NodeValidationError
        )
        
        workflow = {
            "name": "Test Workflow",
            "nodes": [
                {
                    "id": "agent-1",
                    "type": "agent",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Agent 1"}
                },
                {
                    "id": "agent-1",  # Duplicate ID
                    "type": "agent",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "Agent 2"}
                }
            ],
            "edges": []
        }
        
        with pytest.raises(NodeValidationError) as exc_info:
            workflow_translator.translate(workflow)
        
        assert "duplicate" in str(exc_info.value).lower()


class TestNodeConfigGenerator:
    """Test node config generator."""
    
    def test_get_agent_schema(self):
        """Test getting agent node schema."""
        from app.workflow.node_config_generator import node_config_generator
        
        schema = node_config_generator.get_node_schema("agent")
        
        assert "schema" in schema
        assert "uiSchema" in schema
        assert schema["schema"]["type"] == "object"
        assert "label" in schema["schema"]["properties"]
        assert "instructions" in schema["schema"]["properties"]
    
    def test_get_llm_schema(self):
        """Test getting LLM node schema."""
        from app.workflow.node_config_generator import node_config_generator
        
        schema = node_config_generator.get_node_schema("llm")
        
        assert "schema" in schema
        assert "model" in schema["schema"]["properties"]
        assert "enum" in schema["schema"]["properties"]["model"]
        assert "gpt-4" in schema["schema"]["properties"]["model"]["enum"]
    
    def test_get_all_node_types(self):
        """Test getting all node types."""
        from app.workflow.node_config_generator import node_config_generator
        
        node_types = node_config_generator.get_all_node_types()
        
        assert len(node_types) > 0
        assert any(nt["type"] == "agent" for nt in node_types)
        assert any(nt["type"] == "llm" for nt in node_types)
        
        # Check structure
        for node_type in node_types:
            assert "type" in node_type
            assert "title" in node_type
            assert "category" in node_type
    
    def test_schema_with_current_data(self):
        """Test schema generation with current data."""
        from app.workflow.node_config_generator import node_config_generator
        
        current_data = {
            "label": "My Agent",
            "instructions": "Custom instructions"
        }
        
        schema = node_config_generator.get_node_schema("agent", current_data)
        
        assert "formData" in schema
        assert schema["formData"]["label"] == "My Agent"
        assert schema["formData"]["instructions"] == "Custom instructions"


class TestEventSchema:
    """Test event schema models."""
    
    def test_create_node_start_event(self):
        """Test creating a node_start event."""
        from app.workflow.event_schema import NodeStartEvent
        
        event = NodeStartEvent(
            data={
                "nodeId": "agent-1",
                "nodeType": "agent",
                "label": "AI Agent"
            }
        )
        
        assert event.event_type == "node_start"
        assert event.data["nodeId"] == "agent-1"
        assert event.timestamp  # Should have timestamp
    
    def test_create_event_factory(self):
        """Test event factory."""
        from app.workflow.event_schema import create_event
        
        event = create_event(
            "node_start",
            {"nodeId": "agent-1", "nodeType": "agent", "label": "AI Agent"}
        )
        
        assert event.event_type == "node_start"
        assert event.data["nodeId"] == "agent-1"
    
    def test_event_serialization(self):
        """Test event serialization."""
        from app.workflow.event_schema import WorkflowStartedEvent
        
        event = WorkflowStartedEvent(
            data={
                "execution_id": "exec-123",
                "workflow_name": "Test Workflow"
            }
        )
        
        event_dict = event.model_dump()
        
        assert event_dict["event_type"] == "workflow_started"
        assert event_dict["data"]["execution_id"] == "exec-123"
        assert "timestamp" in event_dict


@pytest.mark.asyncio
class TestIntegrationFlow:
    """Test complete integration flow."""
    
    async def test_workflow_validation_endpoint(self, client):
        """Test workflow validation endpoint."""
        workflow = {
            "name": "Test Workflow",
            "nodes": [
                {
                    "id": "agent-1",
                    "type": "agent",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Agent"}
                }
            ],
            "edges": []
        }
        
        response = await client.post("/api/v1/workflows/validate", json=workflow)
        
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "workflow" in data
    
    async def test_node_types_endpoint(self, client):
        """Test node types endpoint."""
        response = await client.get("/api/v1/workflows/node-types")
        
        assert response.status_code == 200
        data = response.json()
        assert "node_types" in data
        assert len(data["node_types"]) > 0
    
    async def test_node_schema_endpoint(self, client):
        """Test node schema endpoint."""
        response = await client.get("/api/v1/workflows/node-schema/agent")
        
        assert response.status_code == 200
        data = response.json()
        assert "schema" in data
        assert "uiSchema" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
