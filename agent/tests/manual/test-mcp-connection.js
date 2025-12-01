import { createMCPClient } from '../../src/worker/mcp-client.js';
import logger from '../../src/shared/logger.js';

/**
 * Manual test for MCP server connection
 * Tests basic connectivity to a PostgreSQL MCP server
 */
async function testMCPConnection() {
    let client = null;

    try {
        console.log('='.repeat(60));
        console.log('Testing MCP Server Connection');
        console.log('='.repeat(60));

        // Sample MCP server config (AWS CloudWatch)
        const serverConfig = {
            label: 'aws-cloudwatch',
            mcpConfig: {
                type: 'stdio',
                command: 'uvx',
                args: [
                    '--native-tls',
                    'awslabs.cloudwatch-mcp-server@latest'
                ],
                env: {
                    AWS_PROFILE: 'default',
                    FASTMCP_LOG_LEVEL: 'ERROR'
                }
            }
        };

        console.log('\n1. Creating MCP client...');
        client = createMCPClient(serverConfig);

        console.log('\n2. Connecting to MCP server...');
        await client.connect();
        console.log('   ✓ Connected successfully');

        console.log('\n3. Available tools:');
        const tools = client.getTools();
        console.log(`   Found ${tools.length} tools${tools.length === 0 ? ' (schema validation may have failed)' : ':'}`);
        tools.forEach(tool => {
            console.log(`   - ${tool.name}: ${tool.description || 'No description'}`);
            console.log(`     Has valid schema: ${tool.hasValidSchema ? 'Yes' : 'No (fallback)'}`);
        });
        
        if (tools.length === 0) {
            console.log('   ⚠ No tools discovered via SDK - this is expected for servers with schema issues');
            console.log('   Connection is still valid and tools can be called if you know their names');
        }

        console.log('\n4. Testing AWS CloudWatch tools...');
        
        // Test describe_log_groups (correct tool name)
        const toolName = 'describe_log_groups';
        if (client.hasTool(toolName)) {
            console.log(`   Tool '${toolName}' discovered, calling it...`);
            const result = await client.callTool(toolName, {
                limit: 3
            });
            console.log('   ✓ Tool call successful');
            console.log('   Result:', JSON.stringify(result, null, 2).substring(0, 500) + '...');
        } else {
            console.log(`   Tool ${toolName} not discovered - this should not happen!`);
        }

        console.log('\n' + '='.repeat(60));
        console.log('✓ Test completed successfully');
        console.log('='.repeat(60));

    } catch (error) {
        console.error('\n❌ Test failed:', error.message);
        console.error(error.stack);
        process.exit(1);
    } finally {
        if (client) {
            console.log('\n5. Disconnecting...');
            await client.disconnect();
            console.log('   ✓ Disconnected');
        }
        process.exit(0);
    }
}

// Run the test
testMCPConnection();
