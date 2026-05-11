"""Check current embedding configuration."""
import asyncio

async def check_config():
    """Check what's currently configured."""
    from app.repositories.db_repository import db_repository
    
    print("=" * 70)
    print("CURRENT EMBEDDING CONFIGURATION")
    print("=" * 70)
    
    # Check LLMs
    print("\n1. LLMs Configured:")
    llm_configs = await db_repository.list_llm_configs()
    
    if not llm_configs:
        print("   ❌ No LLMs configured")
    else:
        print(f"   Found {len(llm_configs)} LLM(s):")
        for name, config in llm_configs.items():
            use_for_embeddings = config.get('use_for_embeddings', False)
            icon = "✅" if use_for_embeddings else "  "
            print(f"   {icon} {name}")
            print(f"      Provider: {config.get('provider')}")
            print(f"      Model: {config.get('model')}")
            print(f"      Use for Embeddings: {use_for_embeddings}")
    
    # Check Model Keys
    print("\n2. Model Keys Configured:")
    model_keys = await db_repository.list_model_keys(include_secrets=False)
    
    if not model_keys:
        print("   ❌ No Model Keys configured")
    else:
        print(f"   Found {len(model_keys)} Model Key(s):")
        for mk in model_keys:
            print(f"   • {mk['provider']}")
            print(f"     Has Access Key: {mk.get('has_access_credentials', False)}")
            print(f"     Region: {mk.get('region', 'N/A')}")
    
    # Check what embedding service would use
    print("\n3. What Embedding Service Will Use:")
    
    embedding_llm = None
    for name, config in llm_configs.items():
        if config.get('use_for_embeddings'):
            embedding_llm = (name, config)
            break
    
    if embedding_llm:
        name, config = embedding_llm
        print(f"   ✅ Will use LLM: {name}")
        print(f"      Provider: {config.get('provider')}")
        print(f"      Model: {config.get('model')}")
        
        # Check if Model Key exists for this provider
        provider = config.get('provider')
        has_model_key = False
        for mk in model_keys:
            if mk['provider'].lower() in (provider.lower(), 'aws bedrock', 'bedrock'):
                has_model_key = True
                print(f"   ✅ Model Key found: {mk['provider']}")
                print(f"      Has credentials: {mk.get('has_access_credentials', False)}")
                break
        
        if not has_model_key:
            print(f"   ⚠️  No Model Key found for provider: {provider}")
            print(f"      Will use default AWS credential chain")
    else:
        print(f"   ⚠️  No LLM marked for embeddings")
        print(f"      Will use default settings from config")
    
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    
    if not embedding_llm:
        print("\n❌ ACTION REQUIRED: Mark an LLM for embeddings")
        print("   1. Go to Settings → Language Models")
        print("   2. Edit an AWS Bedrock LLM")
        print("   3. Check 'Use for Embeddings' ✅")
        print("   4. Save")
    
    if not model_keys or not any(mk['provider'].lower() in ('aws bedrock', 'bedrock') for mk in model_keys):
        print("\n❌ ACTION REQUIRED: Configure AWS Bedrock Model Key")
        print("   1. Go to Settings → Model Keys")
        print("   2. Add AWS Bedrock provider")
        print("   3. Enter Access Key ID and Secret Access Key")
        print("   4. Save")
    
    if embedding_llm and model_keys:
        print("\n✅ Configuration looks good!")
        print("   Restart backend to apply changes:")
        print("   docker restart agent-api-agent-api-1")

if __name__ == "__main__":
    asyncio.run(check_config())
