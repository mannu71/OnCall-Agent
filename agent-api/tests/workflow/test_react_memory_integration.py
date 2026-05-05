"""Tests for Memory Manager integration in ReactStrategy.

This module tests that ReactStrategy correctly integrates with the Memory Manager
to provide cross-session recall capabilities.

Requirements: 16.7
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.workflow.strategies.react import ReactStrategy


class TestMemoryManagerIntegration:
    """Test Memory Manager integration in ReactStrategy.
    
    **Validates: Requirements 16.7**
    """
    
    @pytest.fixture
    def mock_knowledge_base(self):
        """Mock knowledge base service."""
        kb = MagicMock()
        kb.search_known_issues = AsyncMock(return_value=[])
        kb.search_similar_patterns = AsyncMock(return_value=[])
        kb.record_analysis = AsyncMock()
        kb.add_known_issue = AsyncMock()
        return kb
    
    @pytest.fixture
    def strategy_with_memory(self, mock_knowledge_base):
        """Create ReactStrategy with mocked memory components."""
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = False
            mock_settings.rate_limit_tracking_enabled = False
            
            with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
                strategy = ReactStrategy()
                return strategy
    
    def test_memory_manager_initialized_in_constructor(self, strategy_with_memory):
        """Test that MemoryManager is initialized with BuiltinMemoryProvider.
        
        **Validates: Requirement 16.7 - Initialize MemoryManager with BuiltinMemoryProvider**
        """
        # Verify MemoryManager was initialized
        assert strategy_with_memory._memory_manager is not None
        
        # Verify it has providers registered
        assert strategy_with_memory._memory_manager.has_providers()
        
        # Verify builtin provider is registered
        provider_names = strategy_with_memory._memory_manager.get_provider_names()
        assert "builtin" in provider_names
    
    @pytest.mark.asyncio
    async def test_prefetch_called_before_execution(self, strategy_with_memory, mock_knowledge_base):
        """Test that prefetch_all() is called before agent execution.
        
        **Validates: Requirement 16.7 - Call prefetch_all() before each turn**
        """
        # Setup mock workflow and context
        workflow = {
            "id": "test-workflow",
            "nodes": [
                {"type": "agent", "data": {"instructions": "Test agent"}},
                {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
            ],
            "edges": [],
        }
        
        context = {
            "execution_id": "test-exec-123",
            "user_query": "Why did the service fail?",
            "mcp_manager": None,
        }
        
        # Mock the agent execution to avoid actual LLM calls
        with patch.object(strategy_with_memory, '_execute_agent', new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = {
                "final_answer": "Test answer",
                "messages": [],
                "tool_calls": [],
            }
            
            # Mock LLM config resolution
            with patch.object(strategy_with_memory, '_resolve_llm_config', new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "api_key": "test-key",
                }
                
                # Mock tool setup
                with patch.object(strategy_with_memory, '_setup_tools', new_callable=AsyncMock) as mock_setup:
                    mock_setup.return_value = []
                    
                    # Mock LLM building
                    with patch.object(strategy_with_memory, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        # Mock agent building
                        with patch.object(strategy_with_memory, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = MagicMock()
                            
                            # Execute the workflow
                            result = await strategy_with_memory.execute(workflow, context)
        
        # Verify knowledge base search was called (part of prefetch)
        assert mock_knowledge_base.search_known_issues.called
        assert mock_knowledge_base.search_similar_patterns.called
        
        # Verify the query was passed to search
        call_args = mock_knowledge_base.search_known_issues.call_args
        assert "Why did the service fail?" in str(call_args)
    
    @pytest.mark.asyncio
    async def test_sync_called_after_execution(self, strategy_with_memory, mock_knowledge_base):
        """Test that sync_all() is called after turn completion.
        
        **Validates: Requirement 16.7 - Call sync_all() after turn completion**
        """
        # Setup mock workflow and context
        workflow = {
            "id": "test-workflow",
            "nodes": [
                {"type": "agent", "data": {"instructions": "Test agent"}},
                {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
            ],
            "edges": [],
        }
        
        context = {
            "execution_id": "test-exec-123",
            "user_query": "Why did the service fail?",
            "mcp_manager": None,
        }
        
        # Mock the agent execution
        with patch.object(strategy_with_memory, '_execute_agent', new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = {
                "final_answer": "The service failed due to database connection timeout",
                "messages": [],
                "tool_calls": [],
            }
            
            # Mock other dependencies
            with patch.object(strategy_with_memory, '_resolve_llm_config', new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "api_key": "test-key",
                }
                
                with patch.object(strategy_with_memory, '_setup_tools', new_callable=AsyncMock) as mock_setup:
                    mock_setup.return_value = []
                    
                    with patch.object(strategy_with_memory, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy_with_memory, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = MagicMock()
                            
                            # Spy on the memory manager's sync_all method
                            original_sync = strategy_with_memory._memory_manager.sync_all
                            strategy_with_memory._memory_manager.sync_all = AsyncMock(wraps=original_sync)
                            
                            # Execute the workflow
                            result = await strategy_with_memory.execute(workflow, context)
        
        # Verify sync_all was called
        assert strategy_with_memory._memory_manager.sync_all.called
        
        # Verify it was called with the user query and final answer
        call_args = strategy_with_memory._memory_manager.sync_all.call_args
        assert call_args is not None
        # Check that user_content and assistant_content were passed
        assert len(call_args[1]) >= 2  # At least user_content and assistant_content
    
    def test_memory_system_prompts_integrated(self, strategy_with_memory):
        """Test that memory system prompts are integrated into agent.
        
        **Validates: Requirement 16.7 - Integrate memory system prompts into agent**
        """
        # Mock LLM and tools
        mock_llm = MagicMock()
        mock_tools = []
        agent_config = {"instructions": "Test instructions"}
        
        # Mock the create_react_agent function
        with patch('langgraph.prebuilt.create_react_agent') as mock_create:
            mock_create.return_value = MagicMock()
            
            # Build the agent
            agent = strategy_with_memory._build_agent(
                mock_llm,
                mock_tools,
                agent_config,
                has_cloudwatch=False
            )
        
        # Verify create_react_agent was called
        assert mock_create.called
        
        # Get the system prompt that was passed
        call_kwargs = mock_create.call_args[1]
        system_prompt = call_kwargs.get('prompt', '')
        
        # Verify memory system prompt is included
        # The builtin provider adds a "Memory System" section
        assert "Memory System" in system_prompt or "memory" in system_prompt.lower()
    
    @pytest.mark.asyncio
    async def test_memory_prefetch_failure_does_not_block_execution(self, strategy_with_memory, mock_knowledge_base):
        """Test that memory prefetch failures don't block agent execution.
        
        **Validates: Requirement 16.7 - Handle memory failures gracefully**
        """
        # Make knowledge base search fail
        mock_knowledge_base.search_known_issues.side_effect = Exception("KB search failed")
        
        # Setup mock workflow and context
        workflow = {
            "id": "test-workflow",
            "nodes": [
                {"type": "agent", "data": {"instructions": "Test agent"}},
                {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
            ],
            "edges": [],
        }
        
        context = {
            "execution_id": "test-exec-123",
            "user_query": "Test query",
            "mcp_manager": None,
        }
        
        # Mock the agent execution
        with patch.object(strategy_with_memory, '_execute_agent', new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = {
                "final_answer": "Test answer",
                "messages": [],
                "tool_calls": [],
            }
            
            # Mock other dependencies
            with patch.object(strategy_with_memory, '_resolve_llm_config', new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "api_key": "test-key",
                }
                
                with patch.object(strategy_with_memory, '_setup_tools', new_callable=AsyncMock) as mock_setup:
                    mock_setup.return_value = []
                    
                    with patch.object(strategy_with_memory, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy_with_memory, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = MagicMock()
                            
                            # Execute should not raise despite KB failure
                            result = await strategy_with_memory.execute(workflow, context)
        
        # Verify execution completed successfully
        assert result is not None
        assert result["final_answer"] == "Test answer"
    
    @pytest.mark.asyncio
    async def test_memory_sync_failure_does_not_block_execution(self, strategy_with_memory, mock_knowledge_base):
        """Test that memory sync failures don't block agent execution.
        
        **Validates: Requirement 16.7 - Handle memory failures gracefully**
        """
        # Setup mock workflow and context
        workflow = {
            "id": "test-workflow",
            "nodes": [
                {"type": "agent", "data": {"instructions": "Test agent"}},
                {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
            ],
            "edges": [],
        }
        
        context = {
            "execution_id": "test-exec-123",
            "user_query": "Test query",
            "mcp_manager": None,
        }
        
        # Mock the agent execution
        with patch.object(strategy_with_memory, '_execute_agent', new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = {
                "final_answer": "Test answer",
                "messages": [],
                "tool_calls": [],
            }
            
            # Mock other dependencies
            with patch.object(strategy_with_memory, '_resolve_llm_config', new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "api_key": "test-key",
                }
                
                with patch.object(strategy_with_memory, '_setup_tools', new_callable=AsyncMock) as mock_setup:
                    mock_setup.return_value = []
                    
                    with patch.object(strategy_with_memory, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy_with_memory, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = MagicMock()
                            
                            # Make sync_all fail
                            strategy_with_memory._memory_manager.sync_all = AsyncMock(
                                side_effect=Exception("Sync failed")
                            )
                            
                            # Execute should not raise despite sync failure
                            result = await strategy_with_memory.execute(workflow, context)
        
        # Verify execution completed successfully
        assert result is not None
        assert result["final_answer"] == "Test answer"


class TestMemoryManagerFallback:
    """Test fallback behavior when MemoryManager is not available."""
    
    @pytest.mark.asyncio
    async def test_fallback_to_direct_kb_access(self):
        """Test that ReactStrategy falls back to direct KB access if MemoryManager fails.
        
        **Validates: Requirement 16.7 - Graceful fallback when memory system unavailable**
        """
        # Mock settings to disable other features
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = False
            mock_settings.rate_limit_tracking_enabled = False
            
            # Make MemoryManager import fail inside __init__
            with patch('app.core.memory.manager.MemoryManager', side_effect=ImportError("No module")):
                # Create strategy - should fall back gracefully
                strategy = ReactStrategy()
                
                # Verify memory manager is None (fallback mode)
                assert strategy._memory_manager is None
        
        # Setup mock workflow and context
        workflow = {
            "id": "test-workflow",
            "nodes": [
                {"type": "agent", "data": {"instructions": "Test agent"}},
                {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
            ],
            "edges": [],
        }
        
        context = {
            "execution_id": "test-exec-123",
            "user_query": "Test query",
            "mcp_manager": None,
        }
        
        # Mock knowledge base for fallback
        mock_kb = MagicMock()
        mock_kb.search_known_issues = AsyncMock(return_value=[])
        mock_kb.search_similar_patterns = AsyncMock(return_value=[])
        
        # Mock the agent execution
        with patch.object(strategy, '_execute_agent', new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = {
                "final_answer": "Test answer",
                "messages": [],
                "tool_calls": [],
            }
            
            # Mock other dependencies
            with patch.object(strategy, '_resolve_llm_config', new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "api_key": "test-key",
                }
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup:
                    mock_setup.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = MagicMock()
                            
                            # Patch the fallback KB import
                            with patch('app.services.knowledge_base.knowledge_base', mock_kb):
                                # Execute should work with fallback
                                result = await strategy.execute(workflow, context)
        
        # Verify execution completed successfully
        assert result is not None
        assert result["final_answer"] == "Test answer"
        
        # Verify fallback KB was used
        assert mock_kb.search_known_issues.called
        assert mock_kb.search_similar_patterns.called
