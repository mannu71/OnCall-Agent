"""
Workflow Definition Translator: React Flow JSON → Engine Input

Translates visual workflow definitions from the React Flow editor format
into the internal execution engine format.

React Flow Format:
-----------------
{
    "name": "My Workflow",
    "id": "workflow-123",
    "nodes": [
        {
            "id": "agent-1",
            "type": "agent",
            "position": {"x": 100, "y": 200},
            "data": {
                "label": "AI Agent",
                "instructions": "Analyze logs",
                "model": "gpt-4",
                ...
            }
        }
    ],
    "edges": [
        {
            "id": "edge-1",
            "source": "llm-1",
            "target": "agent-1",
            "sourceHandle": "model-output",
            "targetHandle": "model"
        }
    ]
}

Engine Format:
-------------
{
    "name": "My Workflow",
    "id": "workflow-123",
    "nodes": [...],  # Same as React Flow
    "edges": [...],  # Same as React Flow
    "metadata": {
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "version": "1.0"
    }
}

The translator performs:
1. Validation of node types and connections
2. Enrichment with metadata
3. Normalization of node configurations
4. Validation of required fields per node type
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, validator


class NodeValidationError(Exception):
    """Raised when a node configuration is invalid."""
    pass


class EdgeValidationError(Exception):
    """Raised when an edge configuration is invalid."""
    pass


class WorkflowValidationError(Exception):
    """Raised when the overall workflow is invalid."""
    pass


class ReactFlowNode(BaseModel):
    """React Flow node model."""
    id: str
    type: str
    position: Dict[str, float]
    data: Dict[str, Any]


class ReactFlowEdge(BaseModel):
    """React Flow edge model."""
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class ReactFlowWorkflow(BaseModel):
    """React Flow workflow model."""
    name: str
    id: Optional[str] = None
    nodes: List[ReactFlowNode]
    edges: List[ReactFlowEdge]


class EngineWorkflow(BaseModel):
    """Internal engine workflow model."""
    name: str
    id: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowTranslator:
    """Translates React Flow workflows to engine format."""
    
    # Node type validation rules
    NODE_TYPE_REQUIRED_FIELDS = {
        "agent": ["label"],
        "llm": ["label", "model"],
        "tool": ["label", "mcpConfig"],
        "orchestrator": [],  # sqlFile or sqlContent validated separately
        "cloudwatchAnalyzer": ["logGroups", "analysisType"],
        "scheduler": [],
        "chat": [],
        "output": [],
        "wiki": [],
        "memory": [],
        "router": ["label", "routes"],
    }
    
    # Valid node types
    VALID_NODE_TYPES = set(NODE_TYPE_REQUIRED_FIELDS.keys())
    
    # Valid connection rules (source_type -> target_type -> valid_handles)
    CONNECTION_RULES = {
        "llm": {
            "agent": {"sourceHandle": "model-output", "targetHandle": "model"},
            "router": {"sourceHandle": "model-output", "targetHandle": "model"}
        },
        "tool": {
            "orchestrator": {"sourceHandle": "tool-output", "targetHandle": "database"},
            "agent": {"sourceHandle": "tool-output", "targetHandle": "tools"}
        },
        "scheduler": {
            "orchestrator": {},
            "agent": {},
            "cloudwatchAnalyzer": {}
        },
        "orchestrator": {
            "agent": {"sourceHandle": "output", "targetHandle": "input"},
            "wiki": {"sourceHandle": "output", "targetHandle": "msg"}
        },
        "cloudwatchAnalyzer": {
            "agent": {"sourceHandle": "output", "targetHandle": "input"}
        },
        "agent": {
            "output": {"sourceHandle": "agent-output", "targetHandle": "input"},
            "wiki": {"sourceHandle": "agent-output", "targetHandle": "msg"}
        },
        "chat": {
            "agent": {"sourceHandle": "chat-output", "targetHandle": "input"}
        },
        "memory": {
            "agent": {"sourceHandle": "memory-output", "targetHandle": "memory"}
        },
        "router": {
            "agent": {"sourceHandle": "route-output", "targetHandle": "input"}
        }
    }
    
    def __init__(self):
        """Initialize translator."""
        pass
    
    def translate(self, react_flow_workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Translate React Flow workflow to engine format.
        
        Args:
            react_flow_workflow: React Flow workflow dict
            
        Returns:
            Engine workflow dict
            
        Raises:
            WorkflowValidationError: If workflow is invalid
        """
        # Parse and validate React Flow format
        try:
            rf_workflow = ReactFlowWorkflow(**react_flow_workflow)
        except Exception as e:
            raise WorkflowValidationError(f"Invalid React Flow workflow format: {e}")
        
        # Validate nodes
        self._validate_nodes(rf_workflow.nodes)
        
        # Validate edges
        self._validate_edges(rf_workflow.edges, rf_workflow.nodes)
        
        # Build engine workflow
        engine_workflow = EngineWorkflow(
            name=rf_workflow.name,
            id=rf_workflow.id or self._generate_workflow_id(rf_workflow.name),
            nodes=[node.model_dump() for node in rf_workflow.nodes],
            edges=[edge.model_dump() for edge in rf_workflow.edges],
            metadata={
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "updated_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "version": "1.0",
                "node_count": len(rf_workflow.nodes),
                "edge_count": len(rf_workflow.edges)
            }
        )
        
        return engine_workflow.model_dump()
    
    def _validate_nodes(self, nodes: List[ReactFlowNode]) -> None:
        """Validate all nodes in the workflow.
        
        Args:
            nodes: List of React Flow nodes
            
        Raises:
            NodeValidationError: If any node is invalid
        """
        if not nodes:
            raise NodeValidationError("Workflow must have at least one node")
        
        node_ids = set()
        for node in nodes:
            # Check for duplicate IDs
            if node.id in node_ids:
                raise NodeValidationError(f"Duplicate node ID: {node.id}")
            node_ids.add(node.id)
            
            # Validate node type
            if node.type not in self.VALID_NODE_TYPES:
                raise NodeValidationError(
                    f"Invalid node type '{node.type}' for node {node.id}. "
                    f"Valid types: {', '.join(self.VALID_NODE_TYPES)}"
                )
            
            # Validate required fields
            required_fields = self.NODE_TYPE_REQUIRED_FIELDS.get(node.type, [])
            for field in required_fields:
                if field not in node.data or not node.data[field]:
                    raise NodeValidationError(
                        f"Node {node.id} ({node.type}) missing required field: {field}"
                    )
            
            # Special validation for orchestrator nodes
            if node.type == "orchestrator":
                if not node.data.get("sqlFile") and not node.data.get("sqlContent"):
                    raise NodeValidationError(
                        f"Orchestrator node {node.id} must have either sqlFile or sqlContent"
                    )
    
    def _validate_edges(
        self,
        edges: List[ReactFlowEdge],
        nodes: List[ReactFlowNode]
    ) -> None:
        """Validate all edges in the workflow.
        
        Args:
            edges: List of React Flow edges
            nodes: List of React Flow nodes
            
        Raises:
            EdgeValidationError: If any edge is invalid
        """
        # Build node lookup
        node_map = {node.id: node for node in nodes}
        
        edge_ids = set()
        for edge in edges:
            # Check for duplicate IDs
            if edge.id in edge_ids:
                raise EdgeValidationError(f"Duplicate edge ID: {edge.id}")
            edge_ids.add(edge.id)
            
            # Validate source and target exist
            if edge.source not in node_map:
                raise EdgeValidationError(
                    f"Edge {edge.id} references non-existent source node: {edge.source}"
                )
            if edge.target not in node_map:
                raise EdgeValidationError(
                    f"Edge {edge.id} references non-existent target node: {edge.target}"
                )
            
            # Validate connection rules
            source_node = node_map[edge.source]
            target_node = node_map[edge.target]
            
            self._validate_connection(
                edge.id,
                source_node.type,
                target_node.type,
                edge.sourceHandle,
                edge.targetHandle
            )
    
    def _validate_connection(
        self,
        edge_id: str,
        source_type: str,
        target_type: str,
        source_handle: Optional[str],
        target_handle: Optional[str]
    ) -> None:
        """Validate a specific connection.
        
        Args:
            edge_id: Edge ID for error messages
            source_type: Source node type
            target_type: Target node type
            source_handle: Source handle (optional)
            target_handle: Target handle (optional)
            
        Raises:
            EdgeValidationError: If connection is invalid
        """
        # Check if source type can connect to target type
        if source_type not in self.CONNECTION_RULES:
            raise EdgeValidationError(
                f"Edge {edge_id}: Node type '{source_type}' cannot be a connection source"
            )
        
        valid_targets = self.CONNECTION_RULES[source_type]
        if target_type not in valid_targets:
            raise EdgeValidationError(
                f"Edge {edge_id}: Cannot connect '{source_type}' to '{target_type}'. "
                f"Valid targets: {', '.join(valid_targets.keys())}"
            )
        
        # Check handle requirements (if specified in rules)
        required_handles = valid_targets[target_type]
        if required_handles:
            if "sourceHandle" in required_handles:
                expected_source = required_handles["sourceHandle"]
                if source_handle != expected_source:
                    raise EdgeValidationError(
                        f"Edge {edge_id}: Expected source handle '{expected_source}', "
                        f"got '{source_handle}'"
                    )
            
            if "targetHandle" in required_handles:
                expected_target = required_handles["targetHandle"]
                if target_handle != expected_target:
                    raise EdgeValidationError(
                        f"Edge {edge_id}: Expected target handle '{expected_target}', "
                        f"got '{target_handle}'"
                    )
    
    def _generate_workflow_id(self, workflow_name: str) -> str:
        """Generate a workflow ID from the name.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Generated workflow ID
        """
        import re
        # Convert to lowercase, replace spaces with hyphens, remove special chars
        workflow_id = workflow_name.lower()
        workflow_id = re.sub(r'[^a-z0-9\s-]', '', workflow_id)
        workflow_id = re.sub(r'\s+', '-', workflow_id)
        workflow_id = re.sub(r'-+', '-', workflow_id)
        workflow_id = workflow_id.strip('-')
        
        # Add timestamp suffix for uniqueness
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        return f"{workflow_id}-{timestamp}"


# Global translator instance
workflow_translator = WorkflowTranslator()
