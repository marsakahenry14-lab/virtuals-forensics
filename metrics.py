import sqlite3
from collections import Counter
import json

def get_reciprocal_pairs(db_path: str) -> list:
    """
    Connects to SQLite db, extracts (client, provider) from JobCreated table,
    finds reciprocal hiring, excludes self-hiring, sorts by total volume,
    and returns TOP-5 pairs.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Extract pairs
        cursor.execute("SELECT client, provider FROM JobCreated")
        rows = cursor.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()
    
    # Count occurrences of A -> B
    pair_counts = Counter()
    for client, provider in rows:
        if client != provider:
            pair_counts[(client, provider)] += 1
            
    # Find reciprocal pairs and their total volumes (A->B + B->A)
    reciprocal_volumes = {}
    for (client, provider), count_ab in pair_counts.items():
        # Ensure we only process each pair once by ordering the tuple
        if client < provider:
            count_ba = pair_counts.get((provider, client), 0)
            if count_ba > 0:
                reciprocal_volumes[(client, provider)] = count_ab + count_ba
                
    # Sort by total volume descending
    sorted_pairs = sorted(reciprocal_volumes.items(), key=lambda x: x[1], reverse=True)
    
    # Return TOP 5
    top_5 = sorted_pairs[:5]
    
    # Format as JSON-compatible list of dicts
    result = []
    for (client, provider), volume in top_5:
        result.append({
            "pair": [client, provider],
            "total_mutual_jobs": volume
        })
        
    return result
