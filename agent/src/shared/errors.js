/**
 * Custom Error Classes
 * 
 * Provides consistent error handling across the application.
 */

/**
 * Base application error
 */
export class AppError extends Error {
  constructor(message, statusCode = 500, isOperational = true, details = {}) {
    super(message);
    this.name = this.constructor.name;
    this.statusCode = statusCode;
    this.isOperational = isOperational;
    this.details = details;
    this.timestamp = new Date().toISOString();
    
    Error.captureStackTrace(this, this.constructor);
  }

  toJSON() {
    return {
      name: this.name,
      message: this.message,
      statusCode: this.statusCode,
      details: this.details,
      timestamp: this.timestamp
    };
  }
}

/**
 * Validation error (400)
 */
export class ValidationError extends AppError {
  constructor(message, details = {}) {
    super(message, 400, true, details);
  }
}

/**
 * Not found error (404)
 */
export class NotFoundError extends AppError {
  constructor(resource, details = {}) {
    super(`${resource} not found`, 404, true, details);
  }
}

/**
 * Workflow execution error (500)
 */
export class WorkflowExecutionError extends AppError {
  constructor(message, details = {}) {
    super(message, 500, true, details);
  }
}

/**
 * MCP connection error (503)
 */
export class MCPConnectionError extends AppError {
  constructor(serverName, details = {}) {
    super(`Failed to connect to MCP server: ${serverName}`, 503, true, details);
  }
}

/**
 * Timeout error (408)
 */
export class TimeoutError extends AppError {
  constructor(operation, timeout, details = {}) {
    super(`Operation timed out: ${operation} (${timeout}ms)`, 408, true, details);
  }
}

/**
 * Configuration error (500)
 */
export class ConfigurationError extends AppError {
  constructor(message, details = {}) {
    super(message, 500, false, details);
  }
}
