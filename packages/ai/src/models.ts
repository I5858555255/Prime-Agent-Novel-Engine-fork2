import { MODELS } from "./models.generated.js";
import type { Api, KnownProvider, Model, ModelThinkingLevel, Usage } from "./types.js";

const modelRegistry: Map<string, Map<string, Model<Api>>> = new Map();

// Initialize registry from MODELS on module load
for (const [provider, models] of Object.entries(MODELS)) {
	const providerModels = new Map<string, Model<Api>>();
	for (const [id, model] of Object.entries(models)) {
		providerModels.set(id, model as Model<Api>);
	}
	modelRegistry.set(provider, providerModels);
}

type ModelsType = typeof MODELS;

export const PROVIDER_DEFAULT_APIS: Record<string, Api> = {
	"amazon-bedrock": "bedrock-converse-stream",
	anthropic: "anthropic-messages",
	"azure-openai-responses": "azure-openai-responses",
	cerebras: "openai-completions",
	"cloudflare-ai-gateway": "anthropic-messages",
	"cloudflare-workers-ai": "openai-completions",
	deepseek: "openai-completions",
	fireworks: "anthropic-messages",
	"github-copilot": "anthropic-messages",
	google: "google-generative-ai",
	"google-vertex": "google-vertex",
	groq: "openai-completions",
	huggingface: "openai-completions",
	"kimi-coding": "anthropic-messages",
	minimax: "anthropic-messages",
	"minimax-cn": "anthropic-messages",
	mistral: "mistral-conversations",
	moonshotai: "anthropic-messages",
	"moonshotai-cn": "anthropic-messages",
	openai: "openai-responses",
	"openai-codex": "openai-codex-responses",
	openrouter: "openai-completions",
	opencode: "openai-completions",
	"opencode-go": "openai-completions",
	"prime-inference": "openai-completions",
	"vercel-ai-gateway": "anthropic-messages",
	xai: "openai-completions",
	xiaomi: "anthropic-messages",
	"xiaomi-token-plan-ams": "anthropic-messages",
	"xiaomi-token-plan-cn": "anthropic-messages",
	"xiaomi-token-plan-sgp": "anthropic-messages",
	zai: "openai-completions",
};

export type ProviderApiMap = typeof PROVIDER_DEFAULT_APIS;

type ModelApi<TProvider extends string, TModelId extends string> = TProvider extends keyof ModelsType
	? TModelId extends keyof ModelsType[TProvider]
		? ModelsType[TProvider][TModelId] extends { api: infer TApi }
			? TApi extends Api
				? TApi
				: Api
			: Api
		: TProvider extends
					| "openrouter"
					| "prime-inference"
					| "deepseek"
					| "groq"
					| "cerebras"
					| "xai"
					| "cloudflare-workers-ai"
					| "huggingface"
					| "opencode"
					| "opencode-go"
					| "zai"
			? "openai-completions"
			: TProvider extends "github-copilot" | "cloudflare-ai-gateway"
				? TModelId extends
						| `${string}claude${string}`
						| `${string}fable${string}`
						| `${string}haiku${string}`
						| `${string}opus${string}`
						| `${string}sonnet${string}`
					? "anthropic-messages"
					: TModelId extends
								| `${string}gpt${string}`
								| `${string}o1${string}`
								| `${string}o3${string}`
								| `${string}o4${string}`
						? "openai-responses"
						: TProvider extends keyof ProviderApiMap
							? ProviderApiMap[TProvider]
							: Api
				: TProvider extends keyof ProviderApiMap
					? ProviderApiMap[TProvider]
					: Api
	: TProvider extends keyof ProviderApiMap
		? ProviderApiMap[TProvider]
		: Api;

function resolveFallbackApi(provider: string, modelId: string, sampleModel?: Model<Api>): Api {
	if (
		provider === "openrouter" ||
		provider === "prime-inference" ||
		provider === "deepseek" ||
		provider === "groq" ||
		provider === "cerebras" ||
		provider === "xai" ||
		provider === "cloudflare-workers-ai" ||
		provider === "huggingface" ||
		provider === "opencode" ||
		provider === "opencode-go" ||
		provider === "zai"
	) {
		return "openai-completions";
	}

	if (provider === "github-copilot" || provider === "cloudflare-ai-gateway") {
		const lower = modelId.toLowerCase();
		if (
			lower.includes("claude") ||
			lower.includes("fable") ||
			lower.includes("haiku") ||
			lower.includes("opus") ||
			lower.includes("sonnet")
		) {
			return "anthropic-messages";
		}
		if (lower.includes("gpt") || lower.includes("o1") || lower.includes("o3") || lower.includes("o4")) {
			return "openai-responses";
		}
	}

	return PROVIDER_DEFAULT_APIS[provider] ?? sampleModel?.api ?? "openai-completions";
}

export function getModel<
	TProvider extends keyof ModelsType,
	TModelId extends Extract<keyof ModelsType[TProvider], string>,
>(provider: TProvider, modelId: TModelId): Model<ModelApi<TProvider, TModelId>>;

export function getModel<TProvider extends KnownProvider, TModelId extends string>(
	provider: TProvider,
	modelId: TModelId,
): Model<ModelApi<TProvider, TModelId>>;

export function getModel(provider: string, modelId: string): Model<Api> {
	const providerModels = modelRegistry.get(provider);
	const existing = providerModels?.get(modelId);
	if (existing) return existing;

	const sampleModel = providerModels ? Array.from(providerModels.values())[0] : undefined;
	const defaultApi = resolveFallbackApi(provider, modelId, sampleModel);

	return {
		id: modelId,
		name: modelId,
		api: defaultApi,
		provider: provider as KnownProvider,
		baseUrl: sampleModel?.baseUrl ?? "",
		reasoning: true,
		input: ["text", "image"],
		cost: sampleModel?.cost ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: sampleModel?.contextWindow ?? 128000,
		maxTokens: sampleModel?.maxTokens ?? 16384,
		headers: sampleModel?.headers,
		compat: sampleModel?.compat,
	};
}

export function getProviders(): KnownProvider[] {
	return Array.from(modelRegistry.keys()) as KnownProvider[];
}

export function getModels<TProvider extends keyof ModelsType>(
	provider: TProvider,
): Model<ModelApi<TProvider, Extract<keyof ModelsType[TProvider], string>>>[];

export function getModels<TProvider extends KnownProvider>(provider: TProvider): Model<ModelApi<TProvider, string>>[];

export function getModels(provider: string): Model<Api>[] {
	const models = modelRegistry.get(provider);
	return models ? (Array.from(models.values()) as Model<Api>[]) : [];
}

export function supportsFastMode<TApi extends Api>(model: Model<TApi>): boolean {
	return (
		model.provider === "openai-codex" &&
		model.api === "openai-codex-responses" &&
		(model.id === "gpt-5.4" || model.id === "gpt-5.5" || model.id === "gpt-5.6" || model.id.startsWith("gpt-5.6-"))
	);
}

export interface CostOverrides {
	cacheWrite?: number;
}

export function calculateCost<TApi extends Api>(
	model: Model<TApi>,
	usage: Usage,
	overrides?: CostOverrides,
): Usage["cost"] {
	usage.cost.input = (model.cost.input / 1000000) * usage.input;
	usage.cost.output = (model.cost.output / 1000000) * usage.output;
	usage.cost.cacheRead = (model.cost.cacheRead / 1000000) * usage.cacheRead;
	usage.cost.cacheWrite = ((overrides?.cacheWrite ?? model.cost.cacheWrite) / 1000000) * usage.cacheWrite;
	usage.cost.total = usage.cost.input + usage.cost.output + usage.cost.cacheRead + usage.cost.cacheWrite;
	return usage.cost;
}

const EXTENDED_THINKING_LEVELS: ModelThinkingLevel[] = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];

export function getSupportedThinkingLevels<TApi extends Api>(model: Model<TApi>): ModelThinkingLevel[] {
	if (!model.reasoning) return ["off"];

	return EXTENDED_THINKING_LEVELS.filter((level) => {
		const mapped = model.thinkingLevelMap?.[level];
		if (mapped === null) return false;
		if (level === "xhigh" || level === "max") return mapped !== undefined;
		return true;
	});
}

export function clampThinkingLevel<TApi extends Api>(
	model: Model<TApi>,
	level: ModelThinkingLevel,
): ModelThinkingLevel {
	const availableLevels = getSupportedThinkingLevels(model);
	if (availableLevels.includes(level)) return level;

	const requestedIndex = EXTENDED_THINKING_LEVELS.indexOf(level);
	if (requestedIndex === -1) return availableLevels[0] ?? "off";

	for (let i = requestedIndex; i < EXTENDED_THINKING_LEVELS.length; i++) {
		const candidate = EXTENDED_THINKING_LEVELS[i];
		if (availableLevels.includes(candidate)) return candidate;
	}
	for (let i = requestedIndex - 1; i >= 0; i--) {
		const candidate = EXTENDED_THINKING_LEVELS[i];
		if (availableLevels.includes(candidate)) return candidate;
	}
	return availableLevels[0] ?? "off";
}

/**
 * Check if two models are equal by comparing both their id and provider.
 * Returns false if either model is null or undefined.
 */
export function modelsAreEqual<TApi extends Api>(
	a: Model<TApi> | null | undefined,
	b: Model<TApi> | null | undefined,
): boolean {
	if (!a || !b) return false;
	return a.id === b.id && a.provider === b.provider;
}
