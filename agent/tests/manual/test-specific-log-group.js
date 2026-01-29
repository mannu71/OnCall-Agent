/**
 * Test a specific CloudWatch log group
 * 
 * Usage:
 *   node test-specific-log-group.js "/aws/lambda/my-function"
 *   node test-specific-log-group.js "/aws/lambda/my-function" 24 "ERROR"
 */

import { getCloudWatchLogs, analyzeLogsForErrors } from '../../src/tools/cloudwatch/cloudwatch-tools.js';

/**
 * Test a specific log group
 * @param {string} logGroupName - Name of the log group to query
 * @param {number} hoursAgo - Number of hours to look back (default: 1)
 * @param {string} filterPattern - Filter pattern (default: empty)
 * @param {number} limit - Maximum number of logs (default: 100)
 */
async function testLogGroup(logGroupName, hoursAgo = 1, filterPattern = '', limit = 100) {
    console.log('='.repeat(70));
    console.log('CloudWatch Log Group Test');
    console.log('='.repeat(70));
    console.log(`\nLog Group: ${logGroupName}`);
    console.log(`Time Range: Last ${hoursAgo} hour(s)`);
    console.log(`Filter: ${filterPattern || '(none)'}`);
    console.log(`Limit: ${limit} events`);
    console.log('-'.repeat(70));

    try {
        // Fetch logs
        const result = await getCloudWatchLogs({
            logGroupName: logGroupName,
            hoursAgo: hoursAgo,
            filterPattern: filterPattern,
            limit: limit
        });

        if (!result.success) {
            console.error(`\n❌ Error: ${result.error}`);
            process.exit(1);
        }

        if (result.status === 'NO_LOGS_FOUND') {
            console.log(`\n⚠️  ${result.message}`);
            process.exit(0);
        }

        // Display results
        console.log(`\n✅ Found ${result.count} log events\n`);

        // Show first 10 logs
        const logsToShow = Math.min(10, result.logs.length);
        for (let i = 0; i < logsToShow; i++) {
            const log = result.logs[i];
            console.log(`Event ${i + 1}:`);
            console.log(`  Time: ${log.timestamp || 'N/A'}`);
            console.log(`  Stream: ${log.logStreamName || 'N/A'}`);
            const message = log.message || '';
            console.log(`  Message: ${message.substring(0, 200)}${message.length > 200 ? '...' : ''}`);
            console.log();
        }

        if (result.count > logsToShow) {
            console.log(`... and ${result.count - logsToShow} more events\n`);
        }

        // Analyze for errors
        console.log('-'.repeat(70));
        console.log('Error Analysis:');
        console.log('-'.repeat(70));
        
        const analysis = analyzeLogsForErrors(result.logs);
        console.log(`Total logs: ${analysis.totalLogs}`);
        console.log(`Errors found: ${analysis.errorsFound}`);
        
        if (analysis.hasErrors) {
            console.log(`\n⚠️  Found ${analysis.errorsFound} potential error(s):\n`);
            analysis.errors.slice(0, 5).forEach((error, index) => {
                console.log(`Error ${index + 1}:`);
                console.log(`  Time: ${error.timestamp}`);
                console.log(`  Message: ${error.message.substring(0, 150)}${error.message.length > 150 ? '...' : ''}`);
                console.log();
            });
            
            if (analysis.errorsFound > 5) {
                console.log(`... and ${analysis.errorsFound - 5} more errors`);
            }
        } else {
            console.log('✅ No errors detected');
        }

        console.log('\n' + '='.repeat(70));
        console.log('Test completed successfully');
        console.log('='.repeat(70));

    } catch (error) {
        console.error('\n❌ Test failed:', error.message);
        console.error(error.stack);
        process.exit(1);
    }

    process.exit(0);
}

// Parse command line arguments
const args = process.argv.slice(2);

if (args.length === 0) {
    console.log('Usage: node test-specific-log-group.js <log-group-name> [hours-ago] [filter-pattern] [limit]');
    console.log('\nExamples:');
    console.log('  node test-specific-log-group.js "/aws/lambda/my-function"');
    console.log('  node test-specific-log-group.js "/aws/lambda/my-function" 24');
    console.log('  node test-specific-log-group.js "/aws/lambda/my-function" 24 "ERROR"');
    console.log('  node test-specific-log-group.js "/aws/lambda/my-function" 24 "ERROR OR Exception" 50');
    process.exit(1);
}

const logGroupName = args[0];
const hoursAgo = args[1] ? parseInt(args[1]) : 1;
const filterPattern = args[2] || '';
const limit = args[3] ? parseInt(args[3]) : 100;

// Run the test
testLogGroup(logGroupName, hoursAgo, filterPattern, limit);
