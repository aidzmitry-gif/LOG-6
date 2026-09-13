CREATE OR REPLACE FUNCTION logistics.reject_execution_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Shipping execution is immutable';
END;
$$;
CREATE TRIGGER immutable_shipping_execution
BEFORE UPDATE OR DELETE ON logistics.shipping_execution
FOR EACH ROW EXECUTE FUNCTION logistics.reject_execution_mutation();
CREATE TRIGGER no_truncate_shipping_execution
BEFORE TRUNCATE ON logistics.shipping_execution
FOR EACH STATEMENT EXECUTE FUNCTION logistics.reject_execution_mutation();
