import sqlite3


def get_event_funnel(db_path: str = "indexer_cache.db") -> dict:
    result = {
        "JobCreated": 0,
        "JobFunded": 0,
        "JobSubmitted": 0,
        "JobCompleted": 0,
        "PaymentReleased": 0,
        "JobExpired": 0,
    }
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        for table_name in result.keys():
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                result[table_name] = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                result[table_name] = 0
                
    except Exception:
        pass
    finally:
        if conn:
            conn.close()
    
    return result


def get_top_cartels(db_path: str = "indexer_cache.db") -> list:
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        query = """
        SELECT jc.client, jc.provider, COUNT(*) as created_count,
               COUNT(DISTINCT comp.job_id) as completed_count
        FROM JobCreated jc
        LEFT JOIN JobCompleted comp ON jc.job_id = comp.job_id
        GROUP BY jc.client, jc.provider
        ORDER BY created_count DESC
        LIMIT 10
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        result = []
        for client, provider, created_count, completed_count in rows:
            result.append({
                "client": client,
                "provider": provider,
                "created_count": created_count,
                "completed_count": completed_count,
            })
        return result
        
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_empty_deliverables(db_path: str = "indexer_cache.db") -> dict:
    EMPTY_HASH = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    
    result = {
        "total_empty_submitted": 0,
        "completed_with_empty": 0,
        "expired_with_empty": 0,
    }
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='JobSubmitted'")
        if not cursor.fetchone():
            return result
        
        query = """
        SELECT COUNT(*) as total_empty_submitted,
               COUNT(comp.job_id) as completed_with_empty,
               COUNT(exp.job_id) as expired_with_empty
        FROM JobSubmitted s
        LEFT JOIN JobCompleted comp ON s.job_id = comp.job_id
        LEFT JOIN JobExpired exp ON s.job_id = exp.job_id
        WHERE s.deliverable = ?
        """
        cursor.execute(query, (EMPTY_HASH,))
        row = cursor.fetchone()
        
        if row:
            result["total_empty_submitted"] = row[0] or 0
            result["completed_with_empty"] = row[1] or 0
            result["expired_with_empty"] = row[2] or 0
        
    except Exception as e:
        print(f"Error in get_empty_deliverables: {e}")
    finally:
        if conn:
            conn.close()
    
    return result

def get_evaluator_behavior(db_path: str = "indexer_cache.db") -> list:
    EMPTY_HASH = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='JobRejected'")
        has_job_rejected = cursor.fetchone() is not None
        
        if has_job_rejected:
            query = """
            SELECT 
                jc.evaluator,
                COUNT(DISTINCT comp.job_id) + COUNT(DISTINCT rej.job_id) as total_evaluated,
                COUNT(DISTINCT comp.job_id) as approved,
                COUNT(DISTINCT rej.job_id) as rejected,
                COUNT(DISTINCT CASE WHEN s.deliverable = ? THEN comp.job_id END) as empty_approved
            FROM JobCreated jc
            LEFT JOIN JobCompleted comp ON jc.job_id = comp.job_id
            LEFT JOIN JobRejected rej ON jc.job_id = rej.job_id
            LEFT JOIN JobSubmitted s ON jc.job_id = s.job_id
            GROUP BY jc.evaluator
            HAVING total_evaluated >= 3
            ORDER BY total_evaluated DESC
            LIMIT 10
            """
            cursor.execute(query, (EMPTY_HASH,))
        else:
            query = """
            SELECT 
                jc.evaluator,
                COUNT(DISTINCT comp.job_id) as total_evaluated,
                COUNT(DISTINCT comp.job_id) as approved,
                0 as rejected,
                COUNT(DISTINCT CASE WHEN s.deliverable = ? THEN comp.job_id END) as empty_approved
            FROM JobCreated jc
            LEFT JOIN JobCompleted comp ON jc.job_id = comp.job_id
            LEFT JOIN JobSubmitted s ON jc.job_id = s.job_id
            GROUP BY jc.evaluator
            HAVING total_evaluated >= 3
            ORDER BY total_evaluated DESC
            LIMIT 10
            """
            cursor.execute(query, (EMPTY_HASH,))
        
        rows = cursor.fetchall()
        
        result = []
        for row in rows:
            evaluator, total_evaluated, approved, rejected, empty_approved = row
            approval_rate_pct = (approved / total_evaluated * 100) if total_evaluated > 0 else 0.0
            result.append({
                "evaluator": evaluator,
                "total_evaluated": total_evaluated,
                "approved": approved,
                "rejected": rejected,
                "approval_rate_pct": approval_rate_pct,
                "empty_approved": empty_approved,
            })
        return result
        
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []
    finally:
        if conn:
            conn.close()
