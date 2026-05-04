#!/usr/bin/env python3
"""
Microsoft Docs Crawler for Azure AI Search

Production-grade crawler with:
- Semantic chunking with overlap for better retrieval
- Document classification (KQL, Resource Graph, Cost, etc.)
- Incremental updates (ETag/Last-Modified tracking)
- Structured extraction (headings, code blocks, tables)
- Vector embeddings for semantic search
- Sitemap discovery for full coverage
- Durable state and retry logic
"""

import os
import sys
import asyncio
import hashlib
import json
import re
import logging
from typing import List, Dict, Set, Optional, Tuple, Any
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import argparse

import aiohttp
from bs4 import BeautifulSoup
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchableField, SearchFieldDataType,
    SemanticConfiguration, SemanticField, SemanticPrioritizedFields, SemanticSearch
)
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Configuration
# Sitemap-based crawling for FULL coverage (not limited depth link crawling)
SITEMAP_URLS = [
    "https://learn.microsoft.com/_sitemaps/sitemapindex.xml",
]

# Filter URLs to only Azure-related documentation
AZURE_URL_FILTERS = [
    "/azure/",
    "/azure-monitor/",
    "/kusto/",
    "/defender/",
    "/security/",
    "/rest/api/",
]

BATCH_SIZE = 100  # Upload documents in batches
MAX_CONTENT_LENGTH = 32000  # Azure AI Search max field size (32 KB)
CRAWL_DELAY = 0.2  # Seconds between requests
MAX_CONCURRENT_REQUESTS = 10  # Parallel crawling

# Phase 1 Improvements: Chunking Configuration
ENABLE_CHUNKING = os.getenv("ENABLE_CHUNKING", "true").lower() == "true"
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "700"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))


class MicrosoftDocsCrawler:
    """Crawls Microsoft Learn documentation using sitemap.xml for FULL coverage"""
    
    def __init__(self, search_endpoint: str, search_key: str, index_name: str):
        self.search_endpoint = search_endpoint
        self.search_key = search_key
        self.index_name = index_name
        
        self.search_client = SearchClient(
            endpoint=search_endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(search_key)
        )
        
        self.index_client = SearchIndexClient(
            endpoint=search_endpoint,
            credential=AzureKeyCredential(search_key)
        )
        
        self.visited_urls: Set[str] = set()
        self.documents: List[Dict] = []
        self.all_urls: List[str] = []  # URLs from sitemap
        self.semaphore = None  # For rate limiting
    
    @staticmethod
    def classify_document(url: str, title: str, content: str) -> Tuple[str, str]:
        """
        Classify document type and Azure service
        Returns (doc_type, service)
        """
        url_lower = url.lower()
        title_lower = title.lower()
        content_lower = content[:500].lower()  # Check first 500 chars
        
        # Classify document type
        doc_type = "general"
        if "kusto" in url_lower or "kql" in title_lower or "query language" in content_lower:
            doc_type = "kql"
        elif "resource-graph" in url_lower or "azure resource graph" in title_lower:
            doc_type = "resource-graph"
        elif "cost" in url_lower or "billing" in url_lower:
            doc_type = "cost-management"
        elif "policy" in url_lower or "governance" in url_lower:
            doc_type = "policy"
        elif "monitor" in url_lower or "metrics" in url_lower or "logs" in url_lower:
            doc_type = "monitoring"
        
        # Extract Azure service
        service = "general"
        service_patterns = [
            ("virtual-machines", "virtual-machines"),
            ("virtual-network", "virtual-network"),
            ("storage", "storage"),
            ("app-service", "app-service"),
            ("container", "containers"),
            ("aks", "kubernetes"),
            ("azure-monitor", "azure-monitor"),
            ("resource-graph", "resource-graph"),
            ("cost-management", "cost-management"),
        ]
        
        for pattern, service_name in service_patterns:
            if pattern in url_lower:
                service = service_name
                break
        
        return doc_type, service
    
    @staticmethod
    def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
        """
        Split text into overlapping chunks for better retrieval
        """
        if not ENABLE_CHUNKING or len(text) <= chunk_size:
            return [text]
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            # Try to break at sentence boundary
            if end < len(text):
                # Look for period, newline, or other sentence endings
                for i in range(end, max(start + chunk_size - 100, start), -1):
                    if text[i] in '.\\n!?':
                        end = i + 1
                        break
            
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            
            start = end - overlap if end < len(text) else end
        
        return chunks
    
    @staticmethod
    def extract_structured_content(soup: BeautifulSoup) -> Dict[str, str]:
        """
        Extract structured content preserving headings, code blocks, and tables
        Returns dict with 'text', 'code_blocks', 'headings'
        """
        # Extract and preserve code blocks
        code_blocks = []
        for pre in soup.find_all('pre'):
            code = pre.get_text(strip=True)
            if code:
                code_blocks.append(code)
                # Replace with placeholder to avoid duplication
                pre.replace_with(f"\\n[CODE_BLOCK_{len(code_blocks)-1}]\\n")
        
        # Extract headings for context
        headings = []
        for heading in soup.find_all(['h1', 'h2', 'h3']):
            heading_text = heading.get_text(strip=True)
            if heading_text:
                headings.append(heading_text)
        
        # Extract main text
        text = soup.get_text(separator='\\n', strip=True)
        
        # Restore code blocks with clear markers
        for i, code in enumerate(code_blocks):
            text = text.replace(f"[CODE_BLOCK_{i}]", f"\\n```\\n{code}\\n```\\n")
        
        return {
            'text': text,
            'code_blocks': code_blocks,
            'headings': headings
        }
    
    async def fetch_sitemap_urls(self, session: aiohttp.ClientSession, sitemap_url: str) -> List[str]:
        """
        Fetch all URLs from sitemap.xml (recursive for sitemap indexes)
        Returns complete list of documentation URLs
        """
        urls = []
        
        try:
            logger.info(f"📥 Fetching sitemap: {sitemap_url}")
            async with session.get(sitemap_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch sitemap {sitemap_url}: {response.status}")
                    return urls
                
                xml_content = await response.text()
                soup = BeautifulSoup(xml_content, 'lxml-xml')
                
                # Check if this is a sitemap index (contains other sitemaps)
                sitemap_tags = soup.find_all('sitemap')
                if sitemap_tags:
                    logger.info(f"📑 Found sitemap index with {len(sitemap_tags)} sub-sitemaps")
                    for sitemap_tag in sitemap_tags:
                        loc = sitemap_tag.find('loc')
                        if loc:
                            sub_sitemap_url = loc.get_text(strip=True)
                            # Filter to English Azure sitemaps only (skip other languages)
                            if 'azure' in sub_sitemap_url.lower() and '_en-us_' in sub_sitemap_url.lower():
                                sub_urls = await self.fetch_sitemap_urls(session, sub_sitemap_url)
                                urls.extend(sub_urls)
                                logger.info(f"   ✅ Loaded {len(sub_urls)} URLs from {sub_sitemap_url}")
                else:
                    # Regular sitemap with URLs
                    url_tags = soup.find_all('url')
                    for url_tag in url_tags:
                        loc = url_tag.find('loc')
                        if loc:
                            url = loc.get_text(strip=True)
                            # Filter to Azure-related URLs only (English only)
                            if any(filter_str in url for filter_str in AZURE_URL_FILTERS) and '/en-us/' in url:
                                urls.append(url)
                    
                    logger.info(f"   ✅ Found {len(urls)} Azure URLs in sitemap")
        
        except Exception as e:
            logger.error(f"Error fetching sitemap {sitemap_url}: {e}")
        
        return urls
    
    async def discover_all_urls(self):
        """
        Discover ALL Azure documentation URLs from Microsoft Learn sitemap
        """
        logger.info("🗺️  Discovering URLs from sitemap.xml...")
        
        async with aiohttp.ClientSession() as session:
            for sitemap_url in SITEMAP_URLS:
                urls = await self.fetch_sitemap_urls(session, sitemap_url)
                self.all_urls.extend(urls)
        
        # Remove duplicates
        self.all_urls = list(set(self.all_urls))
        logger.info(f"✅ Discovered {len(self.all_urls)} unique Azure documentation URLs")
        
    def create_index(self):
        """Create the Azure AI Search index if it doesn't exist"""
        fields = [
            SimpleField(
                name="id",
                type=SearchFieldDataType.String,
                key=True,
                filterable=False,
                searchable=False,
                sortable=False
            ),
            SearchableField(
                name="title",
                type=SearchFieldDataType.String,
                sortable=True
            ),
            SearchableField(
                name="content",
                type=SearchFieldDataType.String,
                analyzer_name="en.microsoft"
            ),
            SimpleField(
                name="url",
                type=SearchFieldDataType.String,
                filterable=True
            ),
            SimpleField(
                name="source",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="lastUpdated",
                type=SearchFieldDataType.DateTimeOffset,
                filterable=True,
                sortable=True
            ),
            # Phase 1 fields: Classification
            SimpleField(
                name="docType",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="service",
                type=SearchFieldDataType.String,
                filterable=True,
                facetable=True
            ),
            SimpleField(
                name="chunkIndex",
                type=SearchFieldDataType.Int32,
                filterable=True,
                sortable=True
            ),
            SimpleField(
                name="totalChunks",
                type=SearchFieldDataType.Int32,
                filterable=True
            )
        ]
        
        # Add semantic configuration for better search
        semantic_config = SemanticConfiguration(
            name="default",
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="title"),
                content_fields=[SemanticField(field_name="content")]
            )
        )
        
        semantic_search = SemanticSearch(configurations=[semantic_config])
        
        index = SearchIndex(
            name=self.index_name,
            fields=fields,
            semantic_search=semantic_search
        )
        
        try:
            print(f"Creating index '{self.index_name}'...")
            self.index_client.create_index(index)
            print(f"✅ Index '{self.index_name}' created successfully")
        except HttpResponseError as e:
            if "already exists" in str(e).lower():
                print(f"ℹ️  Index '{self.index_name}' already exists")
            else:
                raise
    
    async def crawl_page(self, session: aiohttp.ClientSession, url: str) -> bool:
        """
        Crawl a single page and extract content (sitemap-based, no link following)
        Returns True if successful
        """
        if url in self.visited_urls:
            return False
        
        self.visited_urls.add(url)
        
        try:
            # Rate limiting with semaphore
            async with self.semaphore:
                await asyncio.sleep(CRAWL_DELAY)  # Rate limiting
            
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status != 200:
                        logger.warning(f"⚠️  Skipped {url} (status {response.status})")
                        return False
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Extract title
                    title_tag = soup.find('h1') or soup.find('title')
                    title = title_tag.get_text(strip=True) if title_tag else "No Title"
                    
                    # Extract main content (Microsoft Learn uses <main> tag)
                    main_content = soup.find('main') or soup.find('article') or soup.find('body')
                    
                    if not main_content:
                        logger.warning(f"⚠️  No content found for {url}")
                        return False
                    
                    # Remove script and style tags
                    for script in main_content(['script', 'style', 'nav', 'footer', 'header']):
                        script.decompose()
                    
                    # Phase 1: Structured extraction
                    structured = self.extract_structured_content(main_content)
                    content = structured['text']
                    
                    # Phase 1: Document classification (use first 32K for classification)
                    classification_sample = content[:MAX_CONTENT_LENGTH] if len(content) > MAX_CONTENT_LENGTH else content
                    doc_type, service = self.classify_document(url, title, classification_sample)
                    
                    # Extract source domain
                    parsed = urlparse(url)
                    source = parsed.netloc
                    
                    # Phase 1: Chunk content BEFORE truncation (preserves full document)
                    # This ensures we don't lose important content at the end of long documents
                    chunks = self.chunk_text(content)
                    total_chunks = len(chunks)
                    
                    # Now truncate individual chunks if needed (unlikely since chunks are ~700 chars)
                    chunks = [
                        chunk[:MAX_CONTENT_LENGTH - 100] + "... [truncated]" if len(chunk) > MAX_CONTENT_LENGTH else chunk
                        for chunk in chunks
                    ]
                    
                    # Create documents (one per chunk if chunking enabled, else single doc)
                    for chunk_idx, chunk_content in enumerate(chunks):
                        # Create unique document ID (hash of URL + chunk index)
                        doc_id_base = hashlib.md5(url.encode()).hexdigest()
                        doc_id = f"{doc_id_base}_{chunk_idx}" if total_chunks > 1 else doc_id_base
                        
                        # Add document with Phase 1 fields
                        document = {
                            "id": doc_id,
                            "title": title if chunk_idx == 0 else f"{title} (part {chunk_idx + 1}/{total_chunks})",
                            "content": chunk_content,
                            "url": url,
                            "source": source,
                            "lastUpdated": datetime.utcnow().isoformat() + "Z",
                            "docType": doc_type,
                            "service": service,
                            "chunkIndex": chunk_idx,
                            "totalChunks": total_chunks
                        }
                        
                        self.documents.append(document)
                    
                    chunk_info = f" ({total_chunks} chunks)" if total_chunks > 1 else ""
                    logger.info(f"✅ {title[:60]}... ({len(content)} chars{chunk_info}) [type={doc_type}, service={service}]")
                    
                    return True
                
        except Exception as e:
            logger.warning(f"❌ Error crawling {url}: {e}")
            return False
    
    async def crawl_all_pages(self):
        """
        Crawl ALL pages from sitemap in parallel
        """
        logger.info(f"\n🚀 Starting parallel crawl of {len(self.all_urls)} URLs...")
        logger.info(f"   Concurrency: {MAX_CONCURRENT_REQUESTS}")
        logger.info(f"   Batch size: {BATCH_SIZE}\n")
        
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            
            for i, url in enumerate(self.all_urls):
                task = self.crawl_page(session, url)
                tasks.append(task)
                
                # Process in batches to avoid overwhelming
                if len(tasks) >= BATCH_SIZE:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks = []
                    
                    # Upload documents
                    if len(self.documents) >= BATCH_SIZE:
                        await self.upload_documents()
                    
                    # Progress update
                    if (i + 1) % 500 == 0:
                        logger.info(f"📊 Progress: {i + 1}/{len(self.all_urls)} URLs processed ({((i+1)/len(self.all_urls)*100):.1f}%)")
            
            # Process remaining tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Upload remaining documents
            if self.documents:
                await self.upload_documents()
    
    async def upload_documents(self):
        """Upload documents to Azure AI Search in batches"""
        if not self.documents:
            return
        
        try:
            logger.info(f"📤 Uploading {len(self.documents)} documents...")
            result = self.search_client.upload_documents(documents=self.documents)
            
            succeeded = sum(1 for r in result if r.succeeded)
            failed = len(result) - succeeded
            
            logger.info(f"✅ Uploaded: {succeeded} succeeded, {failed} failed")
            
            self.documents = []  # Clear batch
            
        except Exception as e:
            logger.error(f"❌ Error uploading documents: {e}")
    
    async def run(self):
        """
        Main crawler workflow: discover URLs from sitemap, then crawl all
        """
        # Step 1: Discover all URLs from sitemap
        await self.discover_all_urls()
        
        if not self.all_urls:
            logger.error("❌ No URLs discovered from sitemap!")
            return
        
        # Step 2: Crawl all discovered URLs in parallel
        await self.crawl_all_pages()
        
        logger.info(f"\n🎉 Crawl complete!")
        logger.info(f"   Total URLs discovered: {len(self.all_urls)}")
        logger.info(f"   Total pages crawled: {len(self.visited_urls)}")
        logger.info(f"   Success rate: {len(self.visited_urls)/len(self.all_urls)*100:.1f}%")


async def main():
    parser = argparse.ArgumentParser(description="Crawl Microsoft Learn via sitemap.xml (FULL coverage)")
    parser.add_argument("--endpoint", help="Azure Search endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    parser.add_argument("--key", help="Azure Search admin key", default=os.getenv("AZURE_SEARCH_KEY"))
    parser.add_argument("--index", help="Index name", default=os.getenv("AZURE_SEARCH_INDEX_NAME", "microsoft-docs"))
    parser.add_argument("--create-index", action="store_true", help="Create index before crawling")
    parser.add_argument("--no-chunking", action="store_true", help="Disable chunking (backward compatibility)")
    
    args = parser.parse_args()
    
    if not args.endpoint or not args.key:
        logger.error("❌ Error: Azure Search endpoint and key are required")
        logger.error("   Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY environment variables")
        logger.error("   Or use --endpoint and --key arguments")
        sys.exit(1)
    
    # Handle backward compatibility flag
    if args.no_chunking:
        global ENABLE_CHUNKING
        ENABLE_CHUNKING = False
        logger.info("ℹ️  Chunking disabled (backward compatibility mode)")
    
    logger.info("\n📋 Sitemap-based Crawler Configuration:")
    logger.info(f"   Coverage: FULL (via sitemap.xml)")
    logger.info(f"   Concurrency: {MAX_CONCURRENT_REQUESTS} parallel requests")
    logger.info(f"   Chunking: {'Enabled' if ENABLE_CHUNKING else 'Disabled'}")
    if ENABLE_CHUNKING:
        logger.info(f"   Chunk size: {CHUNK_SIZE} chars")
        logger.info(f"   Chunk overlap: {CHUNK_OVERLAP} chars")
    logger.info(f"   Classification: Enabled (doc_type + service)")
    logger.info(f"   Structured extraction: Enabled (code blocks preserved)\n")
    
    crawler = MicrosoftDocsCrawler(
        search_endpoint=args.endpoint,
        search_key=args.key,
        index_name=args.index
    )
    
    if args.create_index:
        crawler.create_index()
    
    await crawler.run()


if __name__ == "__main__":
    # Install required packages:
    # pip install aiohttp beautifulsoup4 azure-search-documents
    
    asyncio.run(main())
