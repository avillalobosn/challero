-- Narrow, indexed projections of the authoritative documents.
CREATE TABLE IF NOT EXISTS public.duel_read_heads (name text PRIMARY KEY, value jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS public.duel_collection_entries (
    position integer PRIMARY KEY, folder text NOT NULL, card_name text NOT NULL, row_data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS duel_entries_name ON public.duel_collection_entries(card_name);
CREATE INDEX IF NOT EXISTS duel_entries_folder ON public.duel_collection_entries(folder);
CREATE TABLE IF NOT EXISTS public.duel_card_metadata (name text PRIMARY KEY, info jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS public.duel_catalog_cards (name_key text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS public.duel_spanish_names (id text PRIMARY KEY, name text NOT NULL);
ALTER TABLE public.duel_read_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.duel_collection_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.duel_card_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.duel_catalog_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.duel_spanish_names ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.duel_project_document(doc_name text, doc jsonb)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE cards jsonb;
BEGIN
    -- Match the image-permission trigger lock order, including catalog-only writes.
    PERFORM pg_advisory_xact_lock(6152847003);
    PERFORM pg_advisory_xact_lock(6152847004);
    IF doc_name='collection.json' THEN
        IF doc IS NULL THEN
            DELETE FROM public.duel_collection_entries;
            DELETE FROM public.duel_read_heads WHERE name=doc_name;
        ELSE
            INSERT INTO public.duel_read_heads VALUES (doc_name,jsonb_build_object(
                'settings',doc->'settings','revision',doc->'revision'))
                ON CONFLICT(name) DO UPDATE SET value=excluded.value;
            -- Entry positions preserve stable row IDs across public filtering.
            DELETE FROM public.duel_collection_entries;
            INSERT INTO public.duel_collection_entries
                SELECT ordinal-1,coalesce(entry->>'Folder Name',''),coalesce(entry->>'Card Name',''),entry
                FROM jsonb_array_elements(coalesce(doc->'rows','[]')) WITH ORDINALITY e(entry,ordinal);
        END IF;
    ELSIF doc_name='cache.json' THEN
        IF doc IS NULL THEN
            DELETE FROM public.duel_card_metadata;
            DELETE FROM public.duel_read_heads WHERE name=doc_name;
        ELSE
            cards := coalesce(doc->'cards','{}');
            INSERT INTO public.duel_read_heads VALUES (doc_name,doc-'cards')
                ON CONFLICT(name) DO UPDATE SET value=excluded.value;
            DELETE FROM public.duel_card_metadata WHERE NOT cards ? name;
            INSERT INTO public.duel_card_metadata
                SELECT name,cards->name FROM jsonb_object_keys(cards) name
                ON CONFLICT(name) DO UPDATE SET info=excluded.info
                WHERE duel_card_metadata.info IS DISTINCT FROM excluded.info;
        END IF;
    ELSIF doc_name='catalog.json' THEN
        IF doc IS NULL THEN
            DELETE FROM public.duel_catalog_cards;
            DELETE FROM public.duel_read_heads WHERE name=doc_name;
        ELSE
            INSERT INTO public.duel_read_heads VALUES (doc_name,jsonb_build_object('updated',doc->'updated'))
                ON CONFLICT(name) DO UPDATE SET value=excluded.value;
            CREATE TEMP TABLE IF NOT EXISTS duel_catalog_stage (name_key text PRIMARY KEY,data jsonb NOT NULL) ON COMMIT DROP;
            TRUNCATE pg_temp.duel_catalog_stage;
            INSERT INTO pg_temp.duel_catalog_stage
                SELECT DISTINCT ON (lower(card->>'name')) lower(card->>'name'),card
                FROM jsonb_array_elements(coalesce(doc->'data','[]')) card
                WHERE card->>'name' IS NOT NULL ORDER BY lower(card->>'name'),card->>'id';
            DELETE FROM public.duel_catalog_cards WHERE name_key NOT IN (SELECT name_key FROM pg_temp.duel_catalog_stage);
            INSERT INTO public.duel_catalog_cards SELECT * FROM pg_temp.duel_catalog_stage
                ON CONFLICT(name_key) DO UPDATE SET data=excluded.data
                WHERE duel_catalog_cards.data IS DISTINCT FROM excluded.data;
        END IF;
    ELSIF doc_name='spanish-names.json' THEN
        cards := coalesce(doc,'{}');
        DELETE FROM public.duel_spanish_names WHERE NOT cards ? id;
        INSERT INTO public.duel_spanish_names SELECT key,value FROM jsonb_each_text(cards)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name
            WHERE duel_spanish_names.name IS DISTINCT FROM excluded.name;
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.duel_project_document(text,jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.duel_documents_project_changed()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.name IN ('collection.json','cache.json','catalog.json','spanish-names.json') THEN
            PERFORM public.duel_project_document(OLD.name,NULL);
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND OLD.name<>NEW.name AND OLD.name IN ('collection.json','cache.json','catalog.json','spanish-names.json') THEN
        PERFORM public.duel_project_document(OLD.name,NULL);
    END IF;
    IF NEW.name IN ('collection.json','cache.json','catalog.json','spanish-names.json') THEN
        IF TG_OP='UPDATE' AND OLD.name=NEW.name THEN
            IF OLD.value IS NOT DISTINCT FROM NEW.value THEN RETURN NEW; END IF;
            IF NEW.name='collection.json' AND OLD.value->'rows' IS NOT DISTINCT FROM NEW.value->'rows' THEN
                INSERT INTO public.duel_read_heads VALUES (NEW.name,jsonb_build_object('settings',NEW.value->'settings','revision',NEW.value->'revision'))
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value;
                RETURN NEW;
            END IF;
            IF NEW.name='cache.json' AND OLD.value->'cards' IS NOT DISTINCT FROM NEW.value->'cards' THEN
                INSERT INTO public.duel_read_heads VALUES (NEW.name,NEW.value-'cards')
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value;
                RETURN NEW;
            END IF;
            IF NEW.name='catalog.json' AND OLD.value->'data' IS NOT DISTINCT FROM NEW.value->'data' THEN
                INSERT INTO public.duel_read_heads VALUES (NEW.name,jsonb_build_object('updated',NEW.value->'updated'))
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value;
                RETURN NEW;
            END IF;
        END IF;
        PERFORM public.duel_project_document(NEW.name,NEW.value);
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.duel_documents_project_changed() FROM PUBLIC;
DROP TRIGGER IF EXISTS duel_documents_read_models ON public.duel_documents;
CREATE TRIGGER duel_documents_read_models AFTER INSERT OR UPDATE OR DELETE ON public.duel_documents
    FOR EACH ROW EXECUTE FUNCTION public.duel_documents_project_changed();

DO $$
DECLARE api_role text; relation_name text;
BEGIN
    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
        FOREACH relation_name IN ARRAY ARRAY['duel_read_heads','duel_collection_entries','duel_card_metadata','duel_catalog_cards','duel_spanish_names'] LOOP
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I',relation_name,api_role);
        END LOOP;
        EXECUTE format('REVOKE ALL ON FUNCTION public.duel_project_document(text,jsonb) FROM %I',api_role);
        EXECUTE format('REVOKE ALL ON FUNCTION public.duel_documents_project_changed() FROM %I',api_role);
    END LOOP;
END;
$$;
