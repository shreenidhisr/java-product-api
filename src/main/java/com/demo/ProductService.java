package com.demo;

import org.springframework.stereotype.Service;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

@Service
public class ProductService {

    private final Map<Long, Product> store = new ConcurrentHashMap<>();
    private final AtomicLong idSequence = new AtomicLong(1);

    public List<Product> findAll() {
        return new ArrayList<>(store.values());
    }

    public Optional<Product> findById(Long id) {
        return Optional.ofNullable(store.get(id));
    }

    public Product create(Product product) {
        long id = idSequence.getAndIncrement();
        product.setId(id);
        store.put(id, product);
        return product;
    }

    public Optional<Product> update(Long id, Product updated) {
        if (!store.containsKey(id)) {
            return Optional.empty();
        }
        updated.setId(id);
        store.put(id, updated);
        return Optional.of(updated);
    }

    public boolean delete(Long id) {
        return store.remove(id) != null;
    }
}
