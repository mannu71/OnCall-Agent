/**
 * Result Formatter
 * 
 * Shared service for formatting workflow execution results consistently
 * across all strategies.
 */

export class ResultFormatter {
  /**
   * Format execution result for display
   * 
   * @param {Object} result - Raw execution result
   * @param {string} type - Result type ('react' or 'orchestrator')
   * @returns {Object} Formatted result
   */
  format(result, type) {
    switch (type) {
      case 'react':
        return this.formatReactResult(result);
      case 'orchestrator':
        return this.formatOrchestratorResult(result);
      default:
        return result;
    }
  }

  /**
   * Format ReAct agent result
   */
  formatReactResult(result) {
    return {
      type: 'react',
      answer: result.finalAnswer,
      conversationLength: result.messages?.length || 0,
      toolCallsCount: this.countToolCalls(result.messages),
      summary: this.extractSummary(result.finalAnswer)
    };
  }

  /**
   * Format Orchestrator result
   */
  formatOrchestratorResult(result) {
    return {
      type: 'orchestrator',
      output: result.output,
      stepsCompleted: result.results?.filter(r => r.success).length || 0,
      stepsFailed: result.results?.filter(r => !r.success).length || 0,
      totalSteps: result.results?.length || 0,
      summary: this.formatOrchestratorSummary(result)
    };
  }

  /**
   * Count tool calls in messages
   */
  countToolCalls(messages) {
    if (!messages) return 0;
    
    return messages.filter(msg => 
      msg.tool_calls && msg.tool_calls.length > 0
    ).length;
  }

  /**
   * Extract summary from answer (first 200 chars)
   */
  extractSummary(answer) {
    if (!answer) return '';
    return answer.length > 200 
      ? answer.substring(0, 200) + '...'
      : answer;
  }

  /**
   * Format orchestrator summary
   */
  formatOrchestratorSummary(result) {
    if (!result.results || result.results.length === 0) {
      return 'No results';
    }

    const successCount = result.results.filter(r => r.success).length;
    const totalCount = result.results.length;

    return `Completed ${successCount}/${totalCount} steps successfully`;
  }

  /**
   * Format result for logging (sanitized)
   */
  formatForLogging(result) {
    const sanitized = { ...result };
    
    // Remove large data
    if (sanitized.messages && sanitized.messages.length > 5) {
      sanitized.messages = [
        ...sanitized.messages.slice(0, 2),
        { truncated: `... ${sanitized.messages.length - 4} messages ...` },
        ...sanitized.messages.slice(-2)
      ];
    }

    // Truncate long answers
    if (sanitized.finalAnswer && sanitized.finalAnswer.length > 500) {
      sanitized.finalAnswer = sanitized.finalAnswer.substring(0, 500) + '...';
    }

    if (sanitized.output && sanitized.output.length > 500) {
      sanitized.output = sanitized.output.substring(0, 500) + '...';
    }

    return sanitized;
  }
}
