-- Rebuilt on writes, never while serving an image. Readers see each rebuild atomically.
CREATE TABLE IF NOT EXISTS public.duel_image_permissions (
    path text PRIMARY KEY,
    public_visible boolean NOT NULL
);
ALTER TABLE public.duel_image_permissions ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.duel_rebuild_image_permissions()
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    cards jsonb;
    entries jsonb;
    folders jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(6152847003);
    SELECT value->'cards' INTO cards FROM public.duel_documents WHERE name='cache.json';
    SELECT value->'rows', value->'settings'->'visible_folders' INTO entries, folders
        FROM public.duel_documents WHERE name='collection.json';
    DELETE FROM public.duel_image_permissions;
    INSERT INTO public.duel_image_permissions (path, public_visible)
    SELECT regexp_replace(cards->(entry->>'Card Name')->>'image', '[.]jpg$', '.webp'),
           bool_or(coalesce(coalesce(folders, '[]'::jsonb) ? (entry->>'Folder Name'), false))
    FROM jsonb_array_elements(coalesce(entries, '[]'::jsonb)) entry
    WHERE cards->(entry->>'Card Name')->>'image' ~ '^assets/[0-9]+[.](jpg|webp)$'
    GROUP BY 1;
END;
$$;
REVOKE ALL ON FUNCTION public.duel_rebuild_image_permissions() FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.duel_image_permissions_changed()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    doc_name text;
    relevant boolean;
BEGIN
    IF TG_OP='DELETE' THEN doc_name := OLD.name; ELSE doc_name := NEW.name; END IF;
    relevant := doc_name IN ('collection.json','cache.json');
    IF TG_OP='UPDATE' THEN
        relevant := relevant OR OLD.name IN ('collection.json','cache.json');
        IF OLD.name=NEW.name THEN
            IF doc_name='collection.json' AND OLD.value->'rows' IS NOT DISTINCT FROM NEW.value->'rows'
                AND OLD.value->'settings'->'visible_folders' IS NOT DISTINCT FROM NEW.value->'settings'->'visible_folders'
                THEN RETURN NEW;
            END IF;
            IF doc_name='cache.json' AND OLD.value->'cards' IS NOT DISTINCT FROM NEW.value->'cards'
                THEN RETURN NEW;
            END IF;
        END IF;
    END IF;
    IF relevant THEN PERFORM public.duel_rebuild_image_permissions(); END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.duel_image_permissions_changed() FROM PUBLIC;
DROP TRIGGER IF EXISTS duel_documents_image_permissions ON public.duel_documents;
CREATE TRIGGER duel_documents_image_permissions
    AFTER INSERT OR UPDATE OR DELETE ON public.duel_documents
    FOR EACH ROW EXECUTE FUNCTION public.duel_image_permissions_changed();

-- Supabase may grant new public-schema objects to API roles by default.
DO $$
DECLARE api_role text;
BEGIN
    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
        EXECUTE format('REVOKE ALL ON TABLE public.duel_image_permissions FROM %I', api_role);
        EXECUTE format('REVOKE ALL ON FUNCTION public.duel_rebuild_image_permissions() FROM %I', api_role);
        EXECUTE format('REVOKE ALL ON FUNCTION public.duel_image_permissions_changed() FROM %I', api_role);
    END LOOP;
END;
$$;
