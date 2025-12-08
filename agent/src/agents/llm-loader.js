// llm-loader.js
import { ChatOpenAI } from "@langchain/openai";
import { ChatAnthropic } from "@langchain/anthropic";
import { ChatGoogleGenerativeAI } from "@langchain/google-genai";
import { ChatGroq } from "@langchain/groq";

function clampTemperature(t) {
  if (t == null) return 0;
  const n = Number(t);
  if (Number.isNaN(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

// OpenAI reasoning models that don't support temperature parameter
const REASONING_MODELS = ['o1', 'o1-mini', 'o1-preview', 'o3', 'o3-mini', 'o4-mini'];

function isReasoningModel(model) {
  if (!model) return false;
  const modelLower = model.toLowerCase();
  return REASONING_MODELS.some(rm => modelLower.startsWith(rm));
}

export function loadLLM(llmNode) {
  if (!llmNode || !llmNode.data) {
    console.log("⚠️ No LLM node found, using default GPT-4o-mini");
    return new ChatOpenAI({ model: "gpt-4o-mini", temperature: 0 });
  }

  const model = llmNode.data.model;
  const provider = (llmNode.data.provider || "OpenAI").toLowerCase();
  const temperature = clampTemperature(llmNode.data.temperature);
  const apiKey = llmNode.data.apiKey; // optional

  console.log(`🤖 Loading LLM: ${provider} / ${model || "default"}`);

  switch (provider) {
    case "openai":
      if (!model) throw new Error("Missing 'model' for OpenAI provider in LLM node");
      // Reasoning models (o1, o3, o4-mini, etc.) don't support temperature
      const openaiConfig = {
        model,
        ...(apiKey && { apiKey })
      };
      if (!isReasoningModel(model)) {
        openaiConfig.temperature = temperature;
      } else {
        console.log(`ℹ️ Reasoning model ${model} detected - skipping temperature parameter`);
      }
      return new ChatOpenAI(openaiConfig);

    case "anthropic":
      if (!model) throw new Error("Missing 'model' for Anthropic provider in LLM node");
      return new ChatAnthropic({
        model,
        temperature,
        ...(apiKey && { anthropicApiKey: apiKey })
      });

    case "gemini":
    case "google":
      if (!model) throw new Error("Missing 'model' for Google provider in LLM node");
      return new ChatGoogleGenerativeAI({
        model,
        temperature,
        ...(apiKey && { apiKey })
      });

    case "groq":
      if (!model) throw new Error("Missing 'model' for Groq provider in LLM node");
      return new ChatGroq({
        model,
        temperature,
        ...(apiKey && { apiKey })
      });

    case "azure openai":
    case "azure":
      if (!model) throw new Error("Missing 'model' (deployment name) for Azure OpenAI in LLM node");
      return new ChatOpenAI({
        model,
        temperature,
        azureOpenAIApiKey: apiKey,
        azureOpenAIApiDeploymentName: model,
        azureOpenAIApiVersion: llmNode.data.azureVersion || "2024-02-15-preview",
        ...(llmNode.data.endpoint && {
          configuration: { baseURL: llmNode.data.endpoint }
        })
      });

    case "ollama":
      if (!model) throw new Error("Missing 'model' for Ollama provider in LLM node");
      const baseUrl = llmNode.data.baseUrl || "http://localhost:11434";
      return new ChatOpenAI({
        model,
        temperature,
        configuration: { baseURL: `${baseUrl}/v1` }
      });

    default:
      throw new Error("Unsupported LLM provider: " + provider);
  }
}
