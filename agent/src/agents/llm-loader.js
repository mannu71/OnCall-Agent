import { ChatOpenAI } from "@langchain/openai";
import { ChatAnthropic } from "@langchain/anthropic";
import { ChatGoogleGenerativeAI } from "@langchain/google-genai";
import { ChatGroq } from "@langchain/groq";

export function loadLLM(llmNode) {
    if (!llmNode || !llmNode.data) {
        console.log("⚠️ No LLM node found, using default GPT-4o-mini");
        return new ChatOpenAI({ model: "gpt-4o-mini", temperature: 0 });
    }

    const model = llmNode.data.model;
    const provider = (llmNode.data.provider || "OpenAI").toLowerCase();
    const temperature = llmNode.data.temperature || 0;
    const apiKey = llmNode.data.apiKey; // Get API key from node config if available

    console.log(`🤖 Loading LLM: ${provider} / ${model}`);

    switch (provider) {
        case "openai":
            return new ChatOpenAI({ 
                model, 
                temperature,
                ...(apiKey && { apiKey })
            });

        case "anthropic":
            return new ChatAnthropic({ 
                model, 
                temperature,
                ...(apiKey && { anthropicApiKey: apiKey })
            });

        case "gemini":
        case "google":
            return new ChatGoogleGenerativeAI({ 
                model, 
                temperature,
                ...(apiKey && { apiKey })
            });

        case "groq":
            return new ChatGroq({ 
                model, 
                temperature,
                ...(apiKey && { apiKey })
            });

        case "azure openai":
        case "azure":
            return new ChatOpenAI({ 
                model, 
                temperature,
                azureOpenAIApiKey: apiKey,
                azureOpenAIApiDeploymentName: model,
                azureOpenAIApiVersion: "2024-02-15-preview",
                ...(llmNode.data.endpoint && { 
                    configuration: { baseURL: llmNode.data.endpoint }
                })
            });

        case "ollama":
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
